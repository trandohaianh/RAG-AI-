"""Reuse ONE client/channel per caller event loop; never retry a live call check."""
from pathlib import Path
import grpc
from . import call_pb2 as pb, call_pb2_grpc as rpc

class PolicyClient:
    def __init__(self, target, api_key, ca_file, client_cert, client_key):
        self.key = api_key
        self.channel = grpc.aio.secure_channel(target,
            grpc.ssl_channel_credentials(root_certificates=Path(ca_file).read_bytes(),
                private_key=Path(client_key).read_bytes(), certificate_chain=Path(client_cert).read_bytes()),
            options=[("grpc.enable_retries", 0), ("grpc.keepalive_time_ms", 60000),
                     ("grpc.max_receive_message_length", 4096)])
        self.stub = rpc.CallPolicyStub(self.channel)

    async def ready(self):
        # Call during service initialization, NOT on every incoming telephone call.
        import asyncio
        async with asyncio.timeout(5): await self.channel.channel_ready()

    async def check(self, phone_number):
        try:
            return await self.stub.CheckCallAllowed(pb.CheckCallRequest(phone_number=phone_number),
                metadata=(("x-api-key", self.key),), timeout=.028, wait_for_ready=False)
        except grpc.aio.AioRpcError as exc:
            if exc.code() not in {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED,
                                  grpc.StatusCode.RESOURCE_EXHAUSTED}:
                raise  # Explicit gRPC auth/input/programming errors are not policy ALLOW.
            return pb.CheckCallResponse(action=pb.CheckCallResponse.ALLOW,
                policy_status=pb.CheckCallResponse.UNAVAILABLE, hide_status=True, reason="transport_unavailable")

    async def close(self):
        await self.channel.close()
