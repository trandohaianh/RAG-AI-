"""Stateless presentation: API requests only; no Redis, Celery, pandas or ML imports."""
from __future__ import annotations
import json
import os
from urllib.parse import quote
import uuid
import streamlit as st
from api_pool import ApiPool

st.set_page_config(page_title="Edu DSS",layout="wide")
@st.cache_resource
def api_pool():
    return ApiPool(os.environ.get("EDU_API_URL","http://fastapi-backend:8000"))

for key,value in {"token":"","identity":None,"pending":{},"jobs":{},"results":{},"epoch":0}.items():
    if key not in st.session_state: st.session_state[key] = value

def logout():
    for request in st.session_state.pending.values(): request["future"].cancel()
    st.session_state.epoch += 1
    for key,value in {"token":"","identity":None,"pending":{},"jobs":{},"results":{}}.items():
        st.session_state[key] = value

def send(label, method, path, payload=None):
    if len(st.session_state.pending)>=8: st.warning("Please wait for pending API requests"); return
    st.session_state.pending[uuid.uuid4().hex] = dict(label=label,path=path,course=st.session_state.get("course",""),epoch=st.session_state.epoch,
        future=api_pool().submit(method,path,st.session_state.token,payload))

def remember_jobs(data, course):
    if not isinstance(data,dict): return
    if data.get("job_id"):
        st.session_state.jobs[data["job_id"]] = dict(data,_course=course)
    if isinstance(data.get("job"),dict): remember_jobs(data["job"],course)
    for job in data.get("jobs",[]): remember_jobs(job,course)
    while len(st.session_state.jobs)>100: st.session_state.jobs.pop(next(iter(st.session_state.jobs)))

@st.fragment(run_every="2s")
def refresh():
    rerun = False
    for rid,request in list(st.session_state.pending.items()):
        if not request["future"].done(): continue
        del st.session_state.pending[rid]
        if request["epoch"] != st.session_state.epoch: continue
        try: response = request["future"].result()  # already done: never waits for network
        except Exception: st.warning("API request cancelled or unavailable"); continue
        if response.status == 401:
            logout(); st.warning("Identity expired. Sign in again."); st.rerun()
        if response.status >= 400:
            st.error(f"{request['label']}: API returned {response.status}")
            continue
        if request["label"]=="Sign in":
            st.session_state.identity=response.data; rerun=True
        remember_jobs(response.data,request["course"])
        st.session_state.results[request["label"]]=response.data
        while len(st.session_state.results)>20: st.session_state.results.pop(next(iter(st.session_state.results)))
    if rerun: st.rerun()
    active=[jid for jid,j in st.session_state.jobs.items() if j.get("state") not in {"SUCCEEDED","FAILED","SUPERSEDED"}]
    if st.session_state.identity:
        pending_labels={r["label"] for r in st.session_state.pending.values()}
        # Bounded rotating polling: <=4 short requests per fragment, never overlap one job.
        offset=st.session_state.get("poll_offset",0)
        rotated=active[offset:]+active[:offset]
        course=quote(st.session_state.get("course",""),safe="")
        for jid in rotated[:4]:
            course=quote(st.session_state.jobs[jid].get("_course",""),safe="")
            label="Job "+jid
            if label not in pending_labels: send(label,"GET",f"/v1/courses/{course}/jobs/{jid}")
        st.session_state.poll_offset=(offset+4)%max(1,len(active))
    st.caption(f"{len(st.session_state.pending)} requests in flight; {len(active)} active tasks")
    for jid,job in st.session_state.jobs.items():
        with st.expander(f"{job.get('kind','task')} {jid[:10]} - {job.get('state','UNKNOWN')}"):
            st.json(job)
    for label,data in st.session_state.results.items():
        if not label.startswith("Job "):
            with st.expander(label,expanded=True): st.json(data)

st.title("Edu DSS")
if st.session_state.identity is None:
    st.info("Use a short-lived JWT issued by your trusted administrator/identity service.")
    token=st.text_input("Access token",type="password",key="access_token_input")
    if st.button("Sign in"):
        logout(); st.session_state.token=token.strip(); send("Sign in","GET","/v1/me")
    refresh(); st.stop()

actor=st.session_state.identity
st.sidebar.write(f"{actor['principal_id']} / {actor['role']}")
if st.sidebar.button("Sign out"): logout(); st.rerun()
course=st.sidebar.text_input("Course",key="course")
if not course.strip(): st.info("Enter a course identifier"); refresh(); st.stop()
base="/v1/courses/"+quote(course,safe="")
role=actor["role"]
if role in {"instructor","admin"}:
    st.subheader("Instructor")
    dataset=st.text_input("Existing dataset ID")
    uploaded=st.file_uploader("Institutional dataset JSON",type=["json"])
    if st.button("Register dataset") and uploaded:
        try: send("Dataset","POST",base+"/datasets",json.loads(uploaded.getvalue()))
        except ValueError: st.error("Invalid JSON")
    if st.button("Train AutoML"):
        send("Training","POST",base+"/training",{"dataset_id":dataset,"request_id":uuid.uuid4().hex})
    if st.button("Leaderboard"): send("Leaderboard","GET",base+"/leaderboard")
    lecture=st.text_input("Lecture ID",key="lecture_id")
    title=st.text_input("Lecture title")
    text=st.text_area("CMS lecture text")
    if st.button("Prepare explanations offline"):
        send("CMS","POST",base+"/cms",dict(lecture_id=lecture,title=title,text=text))
    version=st.text_input("Reviewed lecture version (from CMS response)")
    if st.button("Approve reviewed lecture"):
        send("Approval","PUT",base+"/lectures/"+quote(lecture,safe="")+"/approval",dict(version=version,approved=True))
if role=="student":
    st.subheader("Student")
    lecture=st.text_input("Lecture ID")
    paragraph=st.text_input("Paragraph ID",value="p1")
    if st.button("Read cached explanation"):
        send("Explanation","GET",base+"/lectures/"+quote(lecture,safe="")+"/paragraphs/"+quote(paragraph,safe=""))
    interests=st.text_input("Learning interests",max_chars=1000)
    if st.button("Request personalized pathway"):
        send("Pathway","POST",base+"/advice",dict(request_id=uuid.uuid4().hex,k=5,interests=interests))
if role=="admin":
    st.subheader("Admin")
    if st.button("Queue state"): send("Queue","GET",base+"/queue")
    if st.button("Worker availability"): send("Workers","GET","/v1/admin/workers")
    if st.button("Knowledge graph"): send("Knowledge graph","GET",base+"/knowledge")
    edges=st.text_area("Knowledge edges JSON",value='[["intro","advanced"]]')
    if st.button("Update knowledge graph"):
        try: send("Knowledge update","PUT",base+"/knowledge",{"edges":json.loads(edges)})
        except ValueError: st.error("Invalid edge JSON")
    principal=st.text_input("Member principal ID")
    member_role=st.selectbox("Member role",["instructor","student"])
    student_id=st.text_input("Student ID (for student membership)")
    if st.button("Grant course membership"):
        send("Membership","POST",base+"/members",dict(principal_id=principal,role=member_role,
            student_id=student_id if member_role=="student" else None,active=True))
refresh()
