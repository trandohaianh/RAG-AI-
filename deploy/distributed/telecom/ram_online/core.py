"""Version-guarded local Bloom + exact Redis membership. Python >=3.11."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import math
import re
import time
import uuid

import redis.asyncio as redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.asyncio.retry import Retry


class OnlineUnavailable(RuntimeError):
    """Unknown/late policy decision. Adapter applies its explicit fail-open policy."""


def normalize_phone(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
        raise ValueError("phone must be canonical E.164, e.g. +84901234567")
    return value


class BloomFilter:
    def __init__(self, capacity: int, error_rate: float = 1e-4):
        if type(capacity) is not int or capacity <= 0 or not 0 < error_rate < 1:
            raise ValueError("positive capacity and error_rate in (0,1) required")
        self.m = math.ceil(-capacity * math.log(error_rate) / math.log(2) ** 2)
        self.k = max(1, round(self.m / capacity * math.log(2)))
        self.bits = bytearray((self.m + 7) // 8)

    def _positions(self, phone):
        digest = hashlib.blake2b(phone.encode("ascii"), digest_size=16).digest()
        a = int.from_bytes(digest[:8], "little")
        b = int.from_bytes(digest[8:], "little") | 1
        return ((a + i * b) % self.m for i in range(self.k))

    def add(self, phone):
        if isinstance(self.bits, bytes):
            raise RuntimeError("published Bloom snapshots are immutable")
        for position in self._positions(phone):
            self.bits[position >> 3] |= 1 << (position & 7)

    def add_many(self, phones):
        for phone in phones:
            self.add(phone)

    def freeze(self):
        self.bits = bytes(self.bits)
        return self

    def __contains__(self, phone):
        return all(self.bits[p >> 3] & (1 << (p & 7)) for p in self._positions(phone))


# Both keys share a Redis Cluster hash tag. Prototype = one policy shard.
CHECK_LUA = """
local manifest = redis.call('GET', KEYS[1])
if not manifest then return -1 end
local token, expected = string.match(manifest, '^(%x+):(%d+)$')
if not token or #token ~= 32 then return -1 end
if redis.call('SCARD', KEYS[2]) ~= tonumber(expected) then return -1 end
if ARGV[2] == '0' and manifest == ARGV[1] then return 0 end
return redis.call('SISMEMBER', KEYS[2], ARGV[3])
"""

INITIALIZE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
if redis.call('SCARD', KEYS[2]) ~= 0 then
    return redis.error_reply('restore the policy manifest before serving')
end
redis.call('SET', KEYS[1], ARGV[1] .. ':0')
return 1
"""

UPDATE_LUA = """
local manifest = redis.call('GET', KEYS[1])
if not manifest then return -1 end
local token, expected = string.match(manifest, '^(%x+):(%d+)$')
if not token or #token ~= 32 then return -1 end
if redis.call('SCARD', KEYS[2]) ~= tonumber(expected) then return -1 end
local changed
if ARGV[2] == '1' then changed = redis.call('SADD', KEYS[2], ARGV[1])
else changed = redis.call('SREM', KEYS[2], ARGV[1]) end
if changed == 1 then
    redis.call('SET', KEYS[1], ARGV[3] .. ':' .. redis.call('SCARD', KEYS[2]))
end
return changed
"""


def make_client(url: str, max_connections=32, io_timeout=.003):
    # No hidden command retries or health-check PING on the request path.
    return redis.Redis.from_url(
        url, decode_responses=True, max_connections=max_connections,
        socket_connect_timeout=io_timeout, socket_timeout=io_timeout,
        retry=Retry(NoBackoff(), 0), retry_on_timeout=False,
        health_check_interval=0, socket_keepalive=True, protocol=2,
    )


@dataclass(frozen=True)
class Snapshot:
    manifest: str
    bloom: BloomFilter


class PolicyStore:
    def __init__(self, client, namespace="telecom-policy"):
        if not re.fullmatch(r"[A-Za-z0-9:_-]+", namespace):
            raise ValueError("invalid namespace")
        self.client = client
        self.manifest_key = "{" + namespace + "}:manifest"
        self.deny_key = "{" + namespace + "}:deny"

    async def initialize_empty_policy(self):
        """Explicit provisioning only. Never call automatically after data loss."""
        await self.client.eval(INITIALIZE_LUA, 2, self.manifest_key,
                               self.deny_key, uuid.uuid4().hex)

    async def set_blocked(self, phone, blocked=True):
        phone = normalize_phone(phone)
        if type(blocked) is not bool:
            raise ValueError("blocked must be a bool")
        result = await self.client.eval(UPDATE_LUA, 2, self.manifest_key,
            self.deny_key, phone, "1" if blocked else "0", uuid.uuid4().hex)
        if result == -1:
            raise OnlineUnavailable("policy missing or inconsistent; restore it")
        return bool(result)


class OnlineGate:
    def __init__(self, policy: PolicyStore, capacity=1_000_000,
                 error_rate=1e-4, budget_ms=5.0, max_inflight=32):
        if not math.isfinite(budget_ms) or budget_ms <= 0:
            raise ValueError("positive finite budget required")
        if type(max_inflight) is not int or max_inflight <= 0:
            raise ValueError("positive max_inflight required")
        self.policy, self.capacity, self.error_rate = policy, capacity, error_rate
        self.budget_ms, self.max_inflight = budget_ms, max_inflight
        self.snapshot = None
        self._inflight = 0
        self._refresh_lock = asyncio.Lock()

    async def refresh_snapshot(self, builder_client=None):
        """Startup/admin path, never invoked by a call check."""
        async with self._refresh_lock:
            client = builder_client or self.policy.client
            before = await client.get(self.policy.manifest_key)
            if before is None:
                raise OnlineUnavailable("policy not provisioned")
            if await client.scard(self.policy.deny_key) > self.capacity:
                raise ValueError("increase Bloom capacity before rebuilding")
            bloom = BloomFilter(self.capacity, self.error_rate)
            cursor = 0
            while True:
                cursor, members = await client.sscan(self.policy.deny_key, cursor, count=512)
                # For large snapshots, build in a separate process/sidecar.
                await asyncio.to_thread(bloom.add_many, members)
                if cursor == 0:
                    break
            after = await client.get(self.policy.manifest_key)
            if before != after:
                return False  # Retain old snapshot; version guard stays correct.
            self.snapshot = Snapshot(before, bloom.freeze())
            return True

    async def check_call_allowed(self, phone_number: str) -> bool:
        started = time.perf_counter()
        phone = normalize_phone(phone_number)
        if self._inflight >= self.max_inflight:
            raise OnlineUnavailable("online capacity exceeded")
        self._inflight += 1
        try:
            snapshot = self.snapshot  # One immutable reference per request.
            maybe = snapshot is None or phone in snapshot.bloom
            manifest = snapshot.manifest if snapshot else ""
            remaining = self.budget_ms / 1000 - (time.perf_counter() - started)
            if remaining <= 0:
                raise OnlineUnavailable("decision deadline exceeded before Redis")
            async with asyncio.timeout(remaining):
                result = await self.policy.client.eval(
                    CHECK_LUA, 2, self.policy.manifest_key, self.policy.deny_key,
                    manifest, "1" if maybe else "0", phone)
            if (time.perf_counter() - started) * 1000 >= self.budget_ms:
                raise OnlineUnavailable("decision arrived after the original deadline")
            if result not in (0, 1):
                raise OnlineUnavailable("policy missing or inconsistent")
            return result == 0
        except (RedisError, TimeoutError) as exc:
            raise OnlineUnavailable("Redis unavailable or lookup deadline exceeded") from exc
        finally:
            self._inflight -= 1

def run(coroutine):
    """Use a process-local precise loop for the 5ms timer on Windows."""
    import sys
    base = asyncio.ProactorEventLoop if sys.platform == "win32" else asyncio.SelectorEventLoop
    class PreciseLoop(base):
        def __init__(self):
            super().__init__()
            self._clock_resolution = time.get_clock_info("perf_counter").resolution
        def time(self):
            return time.perf_counter()
    with asyncio.Runner(loop_factory=PreciseLoop) as runner:
        return runner.run(coroutine)
