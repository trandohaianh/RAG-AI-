"""grpc.aio transport. No REST, JSON, remote auth, retries, or worker queue on call path."""
from __future__ import annotations
import asyncio
from contextlib import AsyncExitStack, suppress
import hmac
import os
import signal
import time
import grpc
from redis.exceptions import RedisError
from . import call_pb2 as pb, call_pb2_grpc as rpc
from .core import OnlineUnavailable, PolicyStore
from .grpc_gate import RamFirstGate, warm_pool
from .resources import owned_redis

class CallPolicyService(rpc.CallPolicyServicer):
    def __init__(self, gate, api_key):
        if len(api_key) < 32: raise ValueError("TELECOM_API_KEY must have at least 32 characters")
        self.gate, self.api_key = gate, api_key

    async def CheckCallAllowed(self, request, context):
        start = time.perf_counter_ns()
        keys = [v for k, v in context.invocation_metadata() if k == "x-api-key"]
        if len(keys) != 1 or not hmac.compare_digest(keys[0].encode(), self.api_key.encode()):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Authentication required")
        remaining = context.time_remaining()
        budget = min(.025, max(0.0, remaining - .001)) if remaining is not None else .025
        try:
            if budget <= 0:
                await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "Caller deadline exhausted")
            async with asyncio.timeout(budget):
                decision = await self.gate.check_call_allowed(request.phone_number)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Canonical E.164 number required")
        except TimeoutError:
            # External cancellation propagates; only our own timeout is converted here.
            from .production import CallDecision
            decision = CallDecision(True, "unavailable", "hidden", "server_budget_exceeded")
        status = {"known": pb.CheckCallResponse.EXACT, "snapshot": pb.CheckCallResponse.SNAPSHOT,
                  "unavailable": pb.CheckCallResponse.UNAVAILABLE}[decision.policy_status]
        return pb.CheckCallResponse(action=pb.CheckCallResponse.ALLOW if decision.allowed else pb.CheckCallResponse.BLOCK,
            policy_status=status, hide_status=decision.display_status == "hidden", reason=decision.reason,
            elapsed_us=(time.perf_counter_ns()-start)//1000)

async def serve():
    key = os.environ["TELECOM_API_KEY"]
    if len(key) < 32: raise ValueError("Unique TELECOM_API_KEY required")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    async with AsyncExitStack() as stack:
        online = await stack.enter_async_context(owned_redis(os.environ["POLICY_REDIS_URL"], io_timeout=.01, max_connections=32))
        builder = await stack.enter_async_context(owned_redis(os.environ["POLICY_REDIS_URL"], io_timeout=1.0, max_connections=2))
        gate = RamFirstGate(PolicyStore(online), lease_seconds=float(os.getenv("BLOOM_LEASE_SECONDS", ".25")))
        async def refresh_loop():
            while True:
                try:
                    async with asyncio.timeout(10):
                        await gate.refresh(builder)
                except (RedisError, OnlineUnavailable, TimeoutError, ValueError):
                    gate.valid_until = 0.0
                await asyncio.sleep(.1)
        # Best effort prewarm is outside request handling; a Redis outage still permits server startup.
        with suppress(RedisError, TimeoutError):
            async with asyncio.timeout(.5): await warm_pool(online)
        refresh_task = asyncio.create_task(refresh_loop(), name="bloom-refresh")
        server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1024),
            ("grpc.max_send_message_length", 4096), ("grpc.so_reuseport", 0),
            ("grpc.keepalive_time_ms", 60000), ("grpc.keepalive_timeout_ms", 10000)], maximum_concurrent_rpcs=256)
        rpc.add_CallPolicyServicer_to_server(CallPolicyService(gate, key), server)
        # h2c is restricted to same-host internal network; public TLS terminates at Nginx.
        if not server.add_insecure_port(os.getenv("GRPC_BIND", "0.0.0.0:50051")):
            raise RuntimeError("gRPC bind failed")
        try:
            await server.start()
            await stopping.wait()
        finally:
            await server.stop(grace=.1)
            refresh_task.cancel()
            with suppress(asyncio.CancelledError): await refresh_task

if __name__ == "__main__":
    asyncio.run(serve())
