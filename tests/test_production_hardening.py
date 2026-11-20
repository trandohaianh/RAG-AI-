"""Operational contracts: fail-open, ownership, startup failure and gateway boundaries."""

import asyncio
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/production"))
from runtime.exec_service import load_secret_files
from runtime.resources import close_async, owned_aiokafka, owned_redis
from telecom.ram_online.core import OnlineUnavailable
from telecom.ram_online.production import CircuitBreaker, FailOpenGate

import rag.cache as cache

PHONE = "+84901234567"


class Gate:
    def __init__(self, value=True):
        self.value, self.calls = value, 0

    async def check_call_allowed(self, phone):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def test_redis_outage_fails_open_and_open_circuit_has_no_io():
    async def run():
        raw = Gate(OnlineUnavailable("unavailable"))
        gate = FailOpenGate(raw, CircuitBreaker(failure_threshold=1))
        first = await gate.check_call_allowed(PHONE)
        assert asdict(first) == dict(
            allowed=True, policy_status="unavailable", display_status="hidden", reason="policy_unavailable"
        )
        for _ in range(100):
            assert (await gate.check_call_allowed(PHONE)).display_status == "hidden"
        assert raw.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("allowed", [True, False])
def test_known_call_policy_preserves_boolean(allowed):
    result = asyncio.run(FailOpenGate(Gate(allowed)).check_call_allowed(PHONE))
    assert result.allowed is allowed and result.policy_status == "known"


def test_half_open_allows_only_one_probe_and_recovery():
    now = [0.0]
    breaker = CircuitBreaker(1, cooldown=1, clock=lambda: now[0])
    breaker.failure(breaker.try_enter())
    assert breaker.try_enter() is None
    now[0] = 1.01
    probe = breaker.try_enter()
    assert probe.probe and breaker.try_enter() is None
    breaker.success(probe)
    assert breaker.state == "closed" and breaker.try_enter() is not None


def test_late_success_cannot_close_newer_open_circuit():
    b = CircuitBreaker(1)
    old, failed = b.try_enter(), b.try_enter()
    b.failure(failed)
    b.success(old)
    assert b.state == "open"


@pytest.mark.parametrize("error", [asyncio.CancelledError(), ValueError("program error")])
def test_cancel_or_program_error_propagates_and_releases_probe(error):
    async def run():
        now = [0.0]
        b = CircuitBreaker(1, cooldown=1, clock=lambda: now[0])
        b.failure(b.try_enter())
        now[0] = 2
        with pytest.raises(type(error)):
            await FailOpenGate(Gate(error), b).check_call_allowed(PHONE)
        assert b.state == "open"

    asyncio.run(run())


def test_invalid_phone_is_rejected_even_when_open():
    b = CircuitBreaker(1)
    b.failure(b.try_enter())
    with pytest.raises(ValueError):
        asyncio.run(FailOpenGate(Gate(), b).check_call_allowed("bad"))


def test_async_redis_closes_client_and_explicit_pool_on_body_error():
    events = []

    class Pool:
        async def aclose(self):
            events.append("pool")

    class Client:
        async def aclose(self):
            events.append("client")

    async def run():
        with pytest.raises(ValueError):
            async with owned_redis(
                "redis://example", pool_factory=lambda *a, **k: Pool(), client_factory=lambda **k: Client()
            ):
                raise ValueError("body")

    asyncio.run(run())
    assert events == ["client", "pool"]


def test_failed_client_construction_still_closes_pool():
    events = []

    class Pool:
        async def aclose(self):
            events.append("pool")

    def fail(**kwargs):
        raise RuntimeError("constructor")

    async def run():
        with pytest.raises(RuntimeError):
            async with owned_redis("redis://example", pool_factory=lambda *a, **k: Pool(), client_factory=fail):
                pass

    asyncio.run(run())
    assert events == ["pool"]


def test_cleanup_failure_does_not_skip_other_resources():
    events = []

    class Pool:
        async def aclose(self):
            events.append("pool")

    class Client:
        async def aclose(self):
            events.append("client")
            raise OSError("close failed")

    async def run():
        async with owned_redis(
            "redis://example", pool_factory=lambda *a, **k: Pool(), client_factory=lambda **k: Client()
        ):
            pass

    asyncio.run(run())
    assert events == ["client", "pool"]


def test_cleanup_timeout_is_bounded():
    async def hang():
        await asyncio.Event().wait()

    asyncio.run(close_async("fake", hang, timeout=0.001))


def test_kafka_start_failure_closes_both_started_and_partial_resources():
    events = []

    class Producer:
        async def start(self):
            events.append("producer-start")

        async def stop(self):
            events.append("producer-stop")

    class Consumer:
        async def start(self):
            events.append("consumer-start")
            raise RuntimeError("start")

        async def stop(self):
            events.append("consumer-stop")

    async def run():
        with pytest.raises(RuntimeError):
            async with owned_aiokafka(Producer, Consumer):
                pass

    asyncio.run(run())
    assert events == ["producer-start", "consumer-start", "consumer-stop", "producer-stop"]


def test_cache_failed_ping_closes_client_and_pool_and_has_cooldown(monkeypatch):
    import redis

    events = []
    pool = SimpleNamespace(disconnect=lambda: events.append("pool"))

    class Client:
        def ping(self):
            raise redis.ConnectionError("private URL must not leak")

        def close(self):
            events.append("client")

    factory = Mock(return_value=pool)
    monkeypatch.setattr(cache, "REDIS_URL", "redis://example")
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_pool", None)
    monkeypatch.setattr(cache, "_retry_after", 0.0)
    monkeypatch.setattr(redis.ConnectionPool, "from_url", factory)
    monkeypatch.setattr(redis, "Redis", lambda **k: Client())
    assert cache._redis() is None and cache._redis() is None
    assert events == ["client", "pool"] and factory.call_count == 1


def test_cache_shutdown_is_idempotent_and_disconnects_even_if_client_close_fails(monkeypatch):
    events = []

    class Client:
        def close(self):
            events.append("client")
            raise OSError("fail")

    monkeypatch.setattr(cache, "_client", Client())
    monkeypatch.setattr(cache, "_pool", SimpleNamespace(disconnect=lambda: events.append("pool")))
    with pytest.raises(OSError):
        cache.close_cache()
    cache.close_cache()
    assert events == ["client", "pool"] and cache._client is None


def test_secret_files_override_ambient_env_without_logging_values(tmp_path):
    secret = tmp_path / "key"
    secret.write_text("private-value\n", encoding="utf-8")
    env = {"KEY": "stale", "KEY_FILE": str(secret)}
    load_secret_files(env)
    assert env["KEY"] == "private-value"
    secret.write_bytes(b"\xef\xbb\xbfprivate-value\n")
    load_secret_files(env)
    assert env["KEY"] == "private-value"
    secret.write_text("bad\nmultiline", encoding="utf-8")
    with pytest.raises(RuntimeError, match="KEY"):
        load_secret_files(env)


def test_compose_only_gateway_publishes_and_workers_are_isolated():
    c = yaml.safe_load((ROOT / "deploy/production/docker-compose.yml").read_text(encoding="utf-8"))
    assert {k: v["ports"] for k, v in c["services"].items() if v.get("ports")} == {"gateway": ["80:80", "443:443"]}
    for name in ["policy_data", "rag_data_net", "telecom_analytics", "edu_analytics"]:
        assert c["networks"][name]["internal"] is True
    for name in ["edu-cpu-worker", "edu-llm-worker"]:
        worker = c["services"][name]
        assert "--pool=prefork" in worker["command"] and worker["mem_limit"] == "2g"
        assert any(x.startswith("--max-memory-per-child=") for x in worker["command"])
        assert not any("edge" in n for n in worker["networks"])
    assert set(c["services"]["gateway"]["networks"]) <= {"ingress", "rag_edge", "telecom_edge", "edu_edge"}


def test_nginx_does_not_log_queries_and_streaming_has_no_buffer():
    text = (ROOT / "deploy/production/nginx/nginx.conf").read_text(encoding="utf-8")
    assert '"uri":"$uri"' in text and '"uri":"$request_uri"' not in text
    assert "proxy_buffering off;" in text and "gzip off;" in text
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in text and "ssl_reject_handshake on;" in text


def test_gateway_ip_is_outside_automatic_pool_and_ca_keys_are_not_mounted():
    import ipaddress

    c = yaml.safe_load((ROOT / "deploy/production/docker-compose.yml").read_text(encoding="utf-8"))
    gateway = c["services"]["gateway"]
    for name, item in gateway["networks"].items():
        if "ipv4_address" in item:
            config = c["networks"][name]["ipam"]["config"][0]
            assert ipaddress.ip_address(item["ipv4_address"]) not in ipaddress.ip_network(config["ip_range"])
    assert "./tls:/etc/nginx/tls:ro" not in gateway["volumes"]
    assert not any(v.startswith("letsencrypt:") for v in gateway["volumes"])
    assert gateway["healthcheck"]["test"][-1] == "http://127.0.0.1:8080/health"
    assert not any("ca_key" in str(s) for svc in c["services"].values() for s in svc.get("secrets", []))
