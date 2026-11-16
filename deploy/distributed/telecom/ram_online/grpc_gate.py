"""RAM fast negatives with an explicit bounded-staleness lease; exact positives."""
from __future__ import annotations
import asyncio
import math
import time
from .core import OnlineGate, normalize_phone
from .production import CallDecision, CircuitBreaker, FailOpenGate

class RamFirstGate:
    def __init__(self, policy, *, lease_seconds=.25, clock=time.monotonic,
                 budget_ms=12.0, max_inflight=32, capacity=1_000_000):
        if not math.isfinite(lease_seconds) or not 0 <= lease_seconds <= 1:
            raise ValueError("Bloom lease must be 0..1 second; 0 disables stale fast negatives")
        self.raw = OnlineGate(policy, capacity=capacity, budget_ms=budget_ms, max_inflight=max_inflight)
        self.exact = FailOpenGate(self.raw, CircuitBreaker(failure_threshold=1, cooldown=.25))
        self.clock, self.lease_seconds, self.valid_until = clock, lease_seconds, 0.0

    async def refresh(self, builder):
        # Lease begins BEFORE the first authoritative read, never after a slow rebuild.
        observed_at = self.clock()
        manifest = await builder.get(self.raw.policy.manifest_key)
        snapshot = self.raw.snapshot
        if manifest is not None and snapshot is not None and manifest == snapshot.manifest:
            self.valid_until = observed_at + self.lease_seconds
            return True
        self.valid_until = 0.0
        if manifest is None:
            return False
        changed = await self.raw.refresh_snapshot(builder)
        if changed:
            self.valid_until = observed_at + self.lease_seconds
        return changed

    async def check_call_allowed(self, phone_number):
        phone = normalize_phone(phone_number)
        snapshot = self.raw.snapshot
        if snapshot is not None and self.clock() < self.valid_until and phone not in snapshot.bloom:
            return CallDecision(True, "snapshot", "hidden", "bloom_negative_leased")
        # One EVAL only, including stale/absent snapshots and Bloom false positives.
        return await self.exact.check_call_allowed(phone)

    @property
    def breaker(self):
        return self.exact.breaker

async def warm_pool(client, count=32):
    # Concurrent held connections actually create N pooled sockets; gather(PING) alone may reuse one.
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        connections = []
        for _ in range(count):
            connection = await client.connection_pool.get_connection()
            stack.push_async_callback(client.connection_pool.release, connection)
            connections.append(connection)
        async def ping(connection):
            await connection.send_command("PING")
            await connection.read_response()
        await asyncio.gather(*[ping(connection) for connection in connections])
