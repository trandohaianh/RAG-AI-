"""Isolated Docker acceptance; no paid AI calls, real customer data or public ports.

Own random project/volumes only; cleanup always removes its own resources. Run after
bootstrap/build. This starts the real stack on loopback ports 18080/18443 and briefly
replaces only the RAG upstream with a labeled SSE fixture for proxy verification.
"""
from __future__ import annotations
import argparse
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time
import uuid
import yaml

DEPLOY=Path(__file__).resolve().parent
REPO=DEPLOY.parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=REPO/"backend/.pytest_production_audit_tmp/smoke")
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    project="ai-final-audit-"+uuid.uuid4().hex[:8]
    base=yaml.safe_load((DEPLOY/"docker-compose.yml").read_text(encoding="utf-8"))
    override=out/"override.yml"
    text="services:\n  gateway:\n    ports: !override [\"127.0.0.1:18080:80\", \"127.0.0.1:18443:443\"]\n    networks:\n"
    for name,n in [("rag_edge",210),("telecom_edge",211),("edu_edge",212)]:
        text+=f"      {name}:\n        ipv4_address: 10.253.{n}.2\n"
    for name,n in [("rag-backend",210),("telecom-api",211),("edu-api",212)]:
        command=[v.replace("172.30.10.2",f"10.253.{n}.2").replace("172.30.11.2",f"10.253.{n}.2").replace("172.30.12.2",f"10.253.{n}.2")
                 for v in base["services"][name]["command"]]
        text+=f"  {name}:\n    command: "+json.dumps(command)+"\n"
    text+="networks:\n"
    for name,n in [("rag_edge",210),("telecom_edge",211),("edu_edge",212)]:
        text+=f"  {name}:\n    ipam:\n      config: !override [{{subnet: 10.253.{n}.0/24, ip_range: 10.253.{n}.128/25}}]\n"
    override.write_text(text,encoding="utf-8")
    compose=["docker","compose","--env-file",str(DEPLOY/".env"),"-p",project,
             "-f",str(DEPLOY/"docker-compose.yml"),"-f",str(override)]
    report={"project":project,"checks":{},"paid_ai_calls":False}
    checks=report["checks"]
    def run(command,*,data=None,timeout=240,check=True):
        result=subprocess.run(command,input=data,text=True,capture_output=True,timeout=timeout,encoding="utf-8",errors="replace")
        if check and result.returncode:
            # Docker logs contain no configured plaintext secret values.
            raise RuntimeError("Command failed: "+" ".join(command[:5])+"\n"+result.stderr[-2000:]+result.stdout[-2000:])
        return result
    def dc(*args,**kwargs):return run(compose+list(args),**kwargs)
    def wait(function,name,timeout=90):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                value=function()
                if value:return value
            except (OSError,http.client.HTTPException,AssertionError):pass
            time.sleep(.5)
        raise TimeoutError(name)
    tls=ssl.create_default_context(cafile=str(DEPLOY/"tls/local-ca.crt"))
    class HTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw=socket.create_connection(("127.0.0.1",18443),timeout=10)
            self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
    def request(host,path,method="GET",body=None,headers=None):
        c=HTTPS(host,18443,context=tls,timeout=10)
        c.request(method,path,body=body,headers=headers or {})
        response=c.getresponse();data=response.read()
        result=(response.status,dict(response.getheaders()),data)
        c.close();return result
    fixture=project+"-sse-fixture"
    created=False
    try:
        dc("config","--quiet")
        created=True
        dc("up","-d","--no-build","--wait","--wait-timeout","240",timeout=300)
        checks["stack_started"]=True
        # Each application's actual non-root image is serving before fixture substitution.
        for host,path in [("api-rag.localhost","/health"),("telecom.localhost","/health"),("edu.localhost","/health")]:
            wait(lambda:request(host,path)[0]==200,host+" health")
        assert request("rag.localhost","/")[0]==200
        checks["real_api_and_web_health"]=True
        h=http.client.HTTPConnection("127.0.0.1",18080,timeout=10)
        h.request("GET","/hello?x=1",headers={"Host":"rag.localhost"})
        response=h.getresponse()
        assert response.status==308 and response.getheader("Location")=="https://rag.localhost/hello?x=1"
        h.close();checks["http_redirect"]=True
        c=HTTPS("api-rag.localhost",18443,context=tls)
        c.request("GET","/health",headers={"Host":"telecom.localhost"})
        assert c.getresponse().status==421;c.close();checks["sni_host_mismatch_rejected"]=True
        try:
            with socket.create_connection(("127.0.0.1",18443),timeout=5) as raw:
                with ssl._create_unverified_context().wrap_socket(raw,server_hostname="unknown.invalid"):pass
            raise AssertionError("unknown SNI accepted")
        except ssl.SSLError:checks["unknown_sni_rejected"]=True
        assert request("api-rag.localhost","/metrics")[0]==404
        checks["metrics_not_public"]=True
        # Real exact policy, followed by real Redis outage/recovery.
        provision = """import os, sys, asyncio
sys.path.insert(0, '/opt')
from runtime.exec_service import load_secret_files
load_secret_files(os.environ)
from ram_online.core import make_client, PolicyStore
async def go():
    client = make_client(os.environ['POLICY_REDIS_URL'], io_timeout=1)
    try:
        policy = PolicyStore(client)
        await policy.initialize_empty_policy()
        await policy.set_blocked('+84901234567', True)
    finally:
        await client.aclose()
asyncio.run(go())
"""
        dc("exec","-T","telecom-api","python","-B","-",data=provision)
        key=(DEPLOY/"secrets/telecom_api_key").read_text().strip()
        def call():
            status,_,body=request("telecom.localhost","/v1/check-call","POST",json.dumps({"phone_number":"+84901234567"}),
                                  {"Content-Type":"application/json","X-API-Key":key})
            assert status==200
            return json.loads(body)
        wait(lambda:call()["allowed"] is False,"exact policy")
        checks["telecom_known_policy"]=True
        dc("stop","redis-policy")
        degraded=call()
        assert degraded["allowed"] is True and degraded["display_status"]=="hidden" and degraded["policy_status"]=="unavailable"
        for _ in range(10):assert call()["allowed"] is True
        checks["redis_outage_fail_open_hidden"]=True
        dc("start","redis-policy")
        wait(lambda:call()["allowed"] is False,"Redis persistence/recovery")
        checks["redis_persistence_recovery"]=True
        # Exercise Kafka mTLS -> real consumer -> analytical Redis Lua.
        now=time.time_ns()//1000000
        event=json.dumps({"caller":"+84901234567","call_id":uuid.uuid4().hex,"setup_ts_ms":now})
        dc("exec","-T","kafka","/opt/kafka/bin/kafka-console-producer.sh","--bootstrap-server","kafka:9093",
           "--producer.config","/run/secrets/kafka_admin_properties","--topic","telecom.call-setup.v1",
           "--property","parse.key=true","--property","key.separator=|",data="+84901234567|"+event+"\n",timeout=45)
        redis_cmd='REDISCLI_AUTH="$(cat /run/secrets/redis_password)"; export REDISCLI_AUTH; exec redis-cli --user app --raw "$@"'
        def counted():
            result=dc("exec","-T","redis-analytics","sh","-ec",redis_cmd,"--","ZCARD","{telecom-window:+84901234567}:calls")
            return result.stdout.strip()=="1"
        wait(counted,"Kafka mTLS consumer/Redis sink",timeout=60)
        checks["kafka_mtls_to_lua_sink"]=True
        negative_tls = """import socket, ssl
context=ssl.create_default_context(cafile='/run/secrets/kafka_ca')
rejected=False
try:
    with socket.create_connection(('kafka',9093),timeout=3) as raw:
        with context.wrap_socket(raw,server_hostname='kafka') as connection:
            connection.sendall(b'\\x00\\x00\\x00\\x00')
            connection.recv(1)
except ssl.SSLError:
    rejected=True
assert rejected, 'broker accepted client without certificate'
try:
    connection=socket.create_connection(('kafka',9094),timeout=2)
except OSError:
    pass
else:
    connection.close()
    raise AssertionError('controller listener reachable from client network')
print('client certificate required; controller is private')
"""
        dc("exec","-T","telecom-consumer","python","-B","-",data=negative_tls,timeout=15)
        checks["kafka_requires_client_cert_and_private_controller"]=True
        # The production worker command/config and actual Linux prefork processes are present.
        inspect_code=("import sys,os,json;sys.path.insert(0,'/opt');from runtime.exec_service import load_secret_files;"
                      "load_secret_files(os.environ);from edudss_engine.worker import celery_app;"
                      "s=celery_app.control.inspect(timeout=5).stats() or {};"
                      "p=[x['pool'] for x in s.values()];"
                      "print(json.dumps({'workers':len(p),'prefork':all('prefork' in x.get('implementation','') for x in p),"
                      "'max_tasks':[x.get('max-tasks-per-child') for x in p]}))")
        worker=dc("exec","-T","edu-cpu-worker","python","-B","-c",inspect_code)
        state=json.loads(worker.stdout)
        assert state["workers"]>=2 and state["prefork"] and all(n==50 for n in state["max_tasks"])
        checks["celery_workers_running"]=state
        # Strict streaming/header behavior is tested with an explicitly synthetic upstream.
        dc("stop","rag-backend")
        run(["docker","run","-d","--name",fixture,"--network",project+"_rag_edge","--network-alias","rag-backend",
             "--read-only","--cap-drop","ALL","--security-opt","no-new-privileges:true",
             "--mount",f"type=bind,src={DEPLOY/'smoke_asgi.py'},dst=/app/smoke_asgi.py,readonly",
             "--entrypoint","uvicorn","ai-systems-telecom:audited","smoke_asgi:app","--host","0.0.0.0","--port","8000","--no-access-log"])
        wait(lambda:request("api-rag.localhost","/headers")[0]==200,"Nginx DNS recreate",timeout=45)
        data=json.loads(request("api-rag.localhost","/headers",headers={"X-Forwarded-For":"192.0.2.99","X-Forwarded-Proto":"http"})[2])
        assert data["x-forwarded-for"]!="192.0.2.99" and data["x-forwarded-proto"]=="https"
        checks["forwarded_headers_overwritten"]=True
        connection=HTTPS("api-rag.localhost",18443,context=tls)
        connection.request("GET","/chat/synthetic",headers={"Accept-Encoding":"gzip"})
        stream=connection.getresponse();start=time.monotonic()
        assert stream.status==200 and stream.getheader("Content-Encoding") is None
        assert stream.readline()==b"data: first\n"
        first=time.monotonic()-start
        assert stream.readline()==b"\n" and stream.readline()==b"data: second\n"
        second=time.monotonic()-start
        assert first<.8 and second>=1.0
        stream.read();connection.close()
        checks["sse_unbuffered"]={"first_frame_seconds":round(first,4),"second_frame_seconds":round(second,4)}
        css=request("api-rag.localhost","/style.css",headers={"Accept-Encoding":"gzip"})
        assert css[1].get("Content-Encoding")=="gzip"
        checks["normal_gzip_enabled"]=True
        run(["docker","rm","-f",fixture]);dc("start","rag-backend")
        wait(lambda:request("api-rag.localhost","/health")[0]==200,"real RAG restored",timeout=60)
        checks["docker_dns_recreate_recovery"]=True
        dc("exec","-T","gateway","nginx","-t","-c","/tmp/nginx.conf")
        dc("exec","-T","gateway","logrotate","--force","--state","/var/log/nginx/.logrotate-state","/etc/logrotate.d/ai-systems")
        dc("kill","-s","HUP","gateway")
        wait(lambda:request("api-rag.localhost","/health")[0]==200,"Nginx reload")
        dc("exec","-T","gateway","sh","-ec", "! grep -q 'Operation not permitted' /var/log/nginx/error.log")
        checks["nginx_config_and_log_rotation"]=True
        checks["nginx_hup_and_worker_signals"]=True
        report["status"]="passed"
    except BaseException as exc:
        report["status"]="failed";report["error"]=str(exc)[-3500:]
        if created:
            logs=dc("logs","--no-color","--tail","60",check=False)
            (out/"containers.log").write_text(logs.stdout+logs.stderr,encoding="utf-8")
        raise
    finally:
        run(["docker","rm","-f",fixture],check=False)
        if created:
            result=dc("down","--volumes","--remove-orphans",check=False,timeout=120)
            report["cleanup"]={"owned_project_removed":result.returncode==0}
        (out/"results.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps({"status":report.get("status"),"checks":len(checks),"report":str(out/"results.json")}))


if __name__=="__main__":main()
