import asyncio
import ast
import json
from pathlib import Path
import time
from unittest.mock import Mock
import fakeredis
import fakeredis.aioredis
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app, split_cms
from api_pool import ApiPool
from edudss_engine.auth import Principal, issue_token
from edudss_engine.runtime import QueueRuntime, RuntimeSettings, Scope
from edudss_engine.storage import Repository

KEY="test-only-key-material-"*4
COURSE="course-1"
BASE="/v1/courses/"+COURSE

def header(actor):return {"Authorization":"Bearer "+issue_token(actor,KEY)}

@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("EDUDSS_ENGINE_DEMO","1")
    server=fakeredis.FakeServer()
    redis=fakeredis.FakeRedis(server=server,decode_responses=True)
    celery=Mock()
    queue=QueueRuntime(RuntimeSettings.from_env(),redis_client=redis,celery=celery)
    repo=Repository(redis,KEY)
    app=create_app(redis_client=redis,queue_runtime=queue,signing_key=KEY)
    admin=Principal("school-1","admin","admin")
    teacher=Principal("school-1","teacher","instructor")
    student=Principal("school-1","student","student","S1")
    repo.set_member(admin,COURSE,"teacher","instructor")
    repo.set_member(admin,COURSE,"student","student","S1")
    with TestClient(app) as client:
        app.state.delivery=fakeredis.aioredis.FakeRedis(server=server,decode_responses=True)
        yield client,repo,queue,admin,teacher,student
    redis.close()

def test_me_requires_signed_identity_and_preserves_roles(ctx):
    http,*_,admin,teacher,student=ctx
    assert http.get("/v1/me").status_code==401
    assert http.get("/v1/me",headers={"Authorization":"Bearer forged"}).status_code==401
    for actor in [admin,teacher,student]:
        r=http.get("/v1/me",headers=header(actor))
        assert r.status_code==200 and r.json()["role"]==actor.role
        assert r.headers["cache-control"]=="no-store"

def test_student_cannot_submit_cms_or_read_admin_graph(ctx):
    http,repo,queue,admin,teacher,student=ctx
    assert http.post(BASE+"/cms",headers=header(student),json={}).status_code==403
    assert http.get(BASE+"/knowledge",headers=header(student)).status_code==403
    assert http.get(BASE+"/knowledge",headers=header(teacher)).status_code==403
    assert http.get(BASE+"/knowledge",headers=header(admin)).status_code==200

def test_cms_enqueues_no_inline_provider_and_approval_is_required(ctx):
    http,repo,queue,admin,teacher,student=ctx
    r=http.post(BASE+"/cms",headers=header(teacher),json=dict(lecture_id="L1",title="Lesson",text="Part A\n\nPart B"))
    assert r.status_code==202
    assert len(r.json()["jobs"])==2 and r.json()["paragraphs"]==["p1","p2"]
    assert queue.celery.send_task.call_count==2
    assert http.get(BASE+"/lectures/L1/paragraphs/p1",headers=header(student)).status_code==404

def test_async_delivery_cache_miss_hit_revocation_and_tenant(ctx):
    http,repo,queue,admin,teacher,student=ctx
    response=http.post(BASE+"/cms",headers=header(teacher),json=dict(lecture_id="L1",title="Lesson",text="Exact lecture"))
    version=response.json()["version"]
    assert http.put(BASE+"/lectures/L1/approval",headers=header(teacher),json=dict(version=version,approved=True)).status_code==200
    url=BASE+"/lectures/L1/paragraphs/p1"
    r=http.get(url,headers=header(student))
    assert r.status_code==202 and r.json()["state"]=="PENDING"
    paragraph=repo.get("lecture",student.school_id,COURSE,"L1")["paragraphs"][0]
    key=queue._paragraph_key(Scope(student.school_id,COURSE,student.principal_id,student.student_id),"L1",version,"p1",paragraph["text_hash"],"v1")
    repo.redis.set(key,json.dumps({"text":"Cached explanation"}))
    r=http.get(url,headers=header(student))
    assert r.status_code==200 and r.json()["explanation"]["text"]=="Cached explanation"
    assert "delivery;dur=" in r.headers["server-timing"]
    outsider=Principal("other-school","student","student","S1")
    assert http.get(url,headers=header(outsider)).status_code==403
    repo.set_member(admin,COURSE,"student","student","S1",active=False)
    assert http.get(url,headers=header(student)).status_code==403
    assert queue.celery.send_task.call_count==1  # reads never start generation

def test_cms_splitter_bounds_and_unicode():
    rows=split_cms("\u6f22"*6000)
    assert all(len(r["text"])<=4000 and len(r["text"].encode())<=10000 for r in rows)
    assert "".join(r["text"] for r in rows)=="\u6f22"*6000
    with pytest.raises(ValueError):split_cms(" ")
    with pytest.raises(ValueError):split_cms("x"*400001)

def test_shared_async_pool_keeps_tokens_and_cookies_isolated():
    observed=[]
    async def handler(request):
        await asyncio.sleep(.001)
        observed.append((request.headers["authorization"],request.headers.get("cookie")))
        return httpx.Response(200,json={"token":request.headers["authorization"]},headers={"set-cookie":"session=should-never-be-sent"})
    pool=ApiPool("http://backend",transport=httpx.MockTransport(handler))
    try:
        first=pool.submit("GET","/v1/me","A").result(timeout=2)
        futures=[pool.submit("GET","/v1/me",token) for token in ["A","B"]*10]
        assert [f.result(timeout=2).data["token"] for f in futures]==["Bearer "+x for x in ["A","B"]*10]
        assert all(cookie is None for _,cookie in observed)
        assert first.data["token"]=="Bearer A"
    finally:pool.close()
    assert not pool.thread.is_alive()

def test_shared_pool_overload_and_external_url_rejected():
    async def handler(request):
        await asyncio.sleep(.1);return httpx.Response(200,json={})
    pool=ApiPool("http://backend",transport=httpx.MockTransport(handler),capacity=1)
    try:
        first=pool.submit("GET","/v1/me","A")
        assert pool.submit("GET","/v1/me","B").result(timeout=1).status==429
        first.result(timeout=2)
        with pytest.raises(ValueError):pool.submit("GET","https://evil.invalid/v1/me","A").result(timeout=2)
    finally:pool.close()

def test_ui_has_no_compute_or_storage_imports_and_no_blocking_poll_loop():
    tree=ast.parse((Path(__file__).resolve().parents[1]/"ui/app.py").read_text())
    imported={node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node,ast.Import)}
    assert not imported & {"pandas","numpy","redis","celery","sklearn","lightgbm"}
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="sleep" for n in ast.walk(tree))
