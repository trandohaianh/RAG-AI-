"""Closed-loop gRPC benchmark. Reports UNKNOWN separately; never certifies a WAN SLA."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import time
import grpc
from ram_online import call_pb2 as pb, call_pb2_grpc as rpc

def percentile(values,p):
    data=sorted(values)
    return round(data[min(len(data)-1,max(0,math.ceil(len(data)*p/100)-1))],4) if data else None

async def benchmark(target,authority,ca,cert,key,api_key,phone,n=1000,concurrency=8):
    credentials=grpc.ssl_channel_credentials(root_certificates=ca,private_key=key,certificate_chain=cert)
    options=[("grpc.enable_retries",0),("grpc.ssl_target_name_override",authority),("grpc.default_authority",authority)]
    async with grpc.aio.secure_channel(target,credentials,options=options) as channel:
        async with asyncio.timeout(8):await channel.channel_ready()
        stub=rpc.CallPolicyStub(channel)
        async def call():return await stub.CheckCallAllowed(pb.CheckCallRequest(phone_number=phone),
            timeout=.028,metadata=(("x-api-key",api_key),),wait_for_ready=False)
        for _ in range(20):await call()
        latencies=[];server=[];counts=Counter();index=0
        async def worker():
            nonlocal index
            while index<n:
                index+=1;started=time.perf_counter()
                try:
                    result=await call()
                    counts[pb.CheckCallResponse.PolicyStatus.Name(result.policy_status)]+=1
                    counts["action_"+pb.CheckCallResponse.Action.Name(result.action)]+=1
                    server.append(result.elapsed_us/1000)
                except grpc.aio.AioRpcError as exc:counts["rpc_"+exc.code().name]+=1
                latencies.append((time.perf_counter()-started)*1000)
        start=time.perf_counter();await asyncio.gather(*[worker() for _ in range(concurrency)]);elapsed=time.perf_counter()-start
        return dict(topology="TLS+mTLS -> Nginx HTTP/2 -> grpc.aio -> Redis when needed",load="closed-loop; warmed channel",
            calls=n,concurrency=concurrency,seconds=round(elapsed,3),rps=round(n/elapsed,1),counts=dict(counts),
            client_ms={str(q):percentile(latencies,q) for q in (50,95,99,99.9,100)},
            server_ms={str(q):percentile(server,q) for q in (50,95,99,99.9,100)},
            over_30ms=sum(v>=30 for v in latencies),sla_certified=False)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target",default="nginx-gateway:443")
    parser.add_argument("--authority",default="telecom.localhost")
    parser.add_argument("--directory",type=Path,default=Path("/audit"))
    parser.add_argument("--phone",default="+84901234567")
    parser.add_argument("--calls",type=int,default=1000)
    parser.add_argument("--concurrency",type=int,default=8)
    a=parser.parse_args();p=a.directory
    print(json.dumps(asyncio.run(benchmark(a.target,a.authority,(p/"tls/local-ca.crt").read_bytes(),
        (p/"secrets/telecom_client_cert").read_bytes(),(p/"secrets/telecom_client_key").read_bytes(),
        (p/"secrets/telecom_api_key").read_text().strip(),a.phone,a.calls,a.concurrency)),indent=2))
