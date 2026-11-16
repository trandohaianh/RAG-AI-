import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from ram_online.core import BloomFilter, Snapshot, PolicyStore, OnlineUnavailable
from ram_online.grpc_gate import RamFirstGate, warm_pool

PHONE="+84901234567"
OTHER="+84901234568"

def gate():
    clock=[10.0]
    redis=SimpleNamespace(eval=AsyncMock(return_value=0))
    result=RamFirstGate(PolicyStore(redis),clock=lambda:clock[0],capacity=1000)
    bloom=BloomFilter(1000);bloom.add(PHONE)
    result.raw.snapshot=Snapshot("a"*32+":1",bloom.freeze());result.valid_until=10.25
    return result,redis,clock

def test_fresh_negative_does_zero_redis_io():
    async def run():
        g,r,_=gate()
        result=await g.check_call_allowed(OTHER)
        assert result.allowed and result.policy_status=="snapshot"
        r.eval.assert_not_awaited()
    asyncio.run(run())

def test_maybe_hit_is_exact_one_atomic_eval_and_stale_negative_falls_back():
    async def run():
        g,r,clock=gate();r.eval.return_value=1
        assert not (await g.check_call_allowed(PHONE)).allowed
        assert r.eval.await_count==1
        clock[0]=11;r.eval.return_value=0
        assert (await g.check_call_allowed(OTHER)).policy_status=="known"
        assert r.eval.await_count==2
    asyncio.run(run())

def test_timeout_opens_circuit_and_does_not_queue_more_redis_calls():
    async def run():
        g,r,clock=gate();clock[0]=11
        from redis.exceptions import TimeoutError
        r.eval.side_effect=TimeoutError("synthetic")
        result=await g.check_call_allowed(PHONE)
        assert result.allowed and result.policy_status=="unavailable"
        for _ in range(100): assert (await g.check_call_allowed(PHONE)).allowed
        assert r.eval.await_count==1
    asyncio.run(run())

def test_lease_not_renewed_after_slow_rebuild():
    async def run():
        g,r,clock=gate()
        async def rebuild(builder):clock[0]=11;return True
        g.raw.refresh_snapshot=rebuild
        builder=SimpleNamespace(get=AsyncMock(return_value="b"*32+":1"))
        await g.refresh(builder)
        assert g.valid_until==10.25 and g.clock()>g.valid_until
    asyncio.run(run())

def test_admission_and_cancellation_release_capacity():
    async def run():
        g,r,_=gate();g.raw.max_inflight=1
        started=asyncio.Event()
        async def slow(*a):started.set();await asyncio.Event().wait()
        r.eval.side_effect=slow
        t=asyncio.create_task(g.check_call_allowed(PHONE));await started.wait()
        result=await g.check_call_allowed(PHONE)
        assert result.allowed and result.policy_status=="unavailable"
        t.cancel()
        with pytest.raises(asyncio.CancelledError):await t
        assert g.raw._inflight==0
    asyncio.run(run())

def test_prewarmer_releases_partial_successful_acquisitions():
    async def run():
        connection=object()
        from redis.exceptions import ConnectionError
        pool=SimpleNamespace(get_connection=AsyncMock(side_effect=[connection,ConnectionError("synthetic")]),release=AsyncMock())
        with pytest.raises(ConnectionError):await warm_pool(SimpleNamespace(connection_pool=pool),2)
        pool.release.assert_awaited_once_with(connection)
    asyncio.run(run())
