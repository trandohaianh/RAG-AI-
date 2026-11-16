"""Near-real-time event-time counting on a SEPARATE analytical Redis."""
from __future__ import annotations

import argparse
from contextlib import AsyncExitStack, suppress
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import signal
import asyncio
import json
import os
import time

from .core import PolicyStore, make_client, normalize_phone, run
from .resources import owned_redis, close_async

WINDOW_LUA = """
local ts = tonumber(ARGV[1])
local previous = redis.call('ZSCORE', KEYS[1], ARGV[2])
if previous and tonumber(previous) ~= ts then
    return redis.error_reply('call_id setup timestamp conflict')
end
local frontier = tonumber(redis.call('GET', KEYS[2]) or '-1')
if ts > frontier then frontier = ts end
local cutoff = frontier - 60000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
if ts > cutoff then redis.call('ZADD', KEYS[1], 'NX', ts, ARGV[2]) end
redis.call('SET', KEYS[2], tostring(frontier), 'PX', 120000)
redis.call('PEXPIRE', KEYS[1], 120000)
return {redis.call('ZCARD', KEYS[1]), frontier}
"""


class SlidingWindowDetector:
    def __init__(self, analytics_client, policy: PolicyStore, threshold=100,
                 namespace="telecom-window", activate_demo_blocks=False):
        if type(threshold) is not int or threshold <= 0:
            raise ValueError("positive threshold required")
        if not namespace.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid window namespace")
        self.analytics, self.policy = analytics_client, policy
        self.threshold, self.namespace = threshold, namespace
        self.activate_demo_blocks = activate_demo_blocks

    async def process(self, event):
        caller = normalize_phone(event["caller"])
        call_id, ts = event["call_id"], event["setup_ts_ms"]
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 128:
            raise ValueError("unique call_id required")
        if type(ts) is not int or ts < 0:
            raise ValueError("setup_ts_ms must be a nonnegative integer")
        now = time.time_ns() // 1_000_000
        if ts > now + 2000:
            raise ValueError("event timestamp is too far in the future")
        tag = "{" + self.namespace + ":" + caller + "}"
        count, frontier = await self.analytics.eval(
            WINDOW_LUA, 2, tag + ":calls", tag + ":frontier", ts, call_id)
        flagged = count > self.threshold
        fresh = 0 <= now - frontier <= 5000
        activated = False
        if flagged and fresh and self.activate_demo_blocks:
            # Illustration only: production needs calibrated policy/allowlists.
            await self.policy.set_blocked(caller, True)
            activated = True
        return {"caller": caller, "calls_60s": count, "frontier_ms": frontier,
                "flagged": flagged, "fresh": fresh, "activated": activated}


async def process_kafka_message(message, detector, consumer, commit=None):
    event = json.loads(message.value())
    caller = normalize_phone(event["caller"])
    if message.key() != caller.encode("ascii"):
        raise ValueError("Kafka key must equal canonical caller")
    result = await detector.process(event)
    if commit is None:
        await asyncio.to_thread(consumer.commit, message=message, asynchronous=False)
    else:
        await commit(message)
    return result


class SerialConsumer:
    """Serialize poll/commit/close; cancellation never closes while poll is running."""
    def __init__(self, consumer):
        self.consumer = consumer
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kafka-owner")

    async def call(self, function, *args, **kwargs):
        future = asyncio.get_running_loop().run_in_executor(self.executor, partial(function, *args, **kwargs))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # poll has an explicit one-second timeout; commit uses bounded librdkafka I/O.
            with suppress(Exception):
                await asyncio.shield(future)
            raise

    async def close(self):
        try:
            await self.call(self.consumer.close)
        finally:
            self.executor.shutdown(wait=False, cancel_futures=False)


async def consume(args):
    from confluent_kafka import Consumer
    config = {}
    security_file = getattr(args, "security_config", None)
    if security_file:
        from pathlib import Path
        config = json.loads(Path(security_file).read_text(encoding="utf-8"))
        allowed = {"security.protocol", "ssl.ca.location", "ssl.certificate.location",
                   "ssl.key.location", "ssl.endpoint.identification.algorithm"}
        if set(config) - allowed or config.get("security.protocol") != "SSL":
            raise ValueError("Expected an explicit TLS client configuration")
    config.update({"bootstrap.servers": args.bootstrap, "group.id": args.group,
                   "enable.auto.commit": False, "enable.auto.offset.store": False,
                   "auto.offset.reset": "earliest", "socket.timeout.ms": 5000,
                   "socket.connection.setup.timeout.ms": 5000})
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    installed = False
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            installed = True
        except (NotImplementedError, RuntimeError):
            pass
        async with AsyncExitStack() as stack:
            online = await stack.enter_async_context(owned_redis(args.policy_redis, io_timeout=1.0))
            analytical = await stack.enter_async_context(owned_redis(args.analytics_redis, io_timeout=1.0, max_connections=8))
            consumer = Consumer(config)
            owner = SerialConsumer(consumer)
            stack.push_async_callback(close_async, "confluent-consumer", owner.close, 15.0)
            await owner.call(consumer.subscribe, [args.topic])
            detector = SlidingWindowDetector(analytical, PolicyStore(online, args.namespace),
                threshold=args.threshold, activate_demo_blocks=args.activate_demo_blocks)
            async def commit(message):
                await owner.call(consumer.commit, message=message, asynchronous=False)
            while True:
                message = await owner.call(consumer.poll, 1.0)
                if message is None:
                    continue
                if message.error():
                    raise RuntimeError("Kafka message error")
                result = await process_kafka_message(message, detector, consumer, commit=commit)
                # Do not print the caller's phone number into production logs.
                print(json.dumps({k: v for k, v in result.items() if k != "caller"}), flush=True)
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", required=True)
    parser.add_argument("--security-config", default=os.environ.get("KAFKA_SECURITY_CONFIG_FILE"))
    parser.add_argument("--topic", default="telecom.call-setup.v1")
    parser.add_argument("--group", default="telecom-window-v1")
    parser.add_argument("--policy-redis", default=os.environ.get("POLICY_REDIS_URL", "redis://localhost:6379"))
    parser.add_argument("--analytics-redis", default=os.environ.get("ANALYTICS_REDIS_URL", "redis://localhost:6380"))
    parser.add_argument("--namespace", default="telecom-policy")
    parser.add_argument("--threshold", type=int, default=100)
    parser.add_argument("--activate-demo-blocks", action="store_true")
    try:
        run(consume(parser.parse_args()))
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
