"""Process-local ownership; no network resources at import or before prefork."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import logging

log = logging.getLogger(__name__)


async def close_async(label, close, timeout=3.0):
    try:
        async with asyncio.timeout(timeout):
            await close()
    except Exception as exc:
        # Keep closing remaining resources; never log credentials/provider text.
        log.warning("Resource cleanup failed: %s (%s)", label, type(exc).__name__)


@asynccontextmanager
async def owned_redis(url, *, io_timeout=0.003, max_connections=32, client_factory=None,
                      pool_factory=None):
    from redis import asyncio as redis
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff
    pool_factory = pool_factory or redis.ConnectionPool.from_url
    client_factory = client_factory or redis.Redis
    async with AsyncExitStack() as stack:
        pool = pool_factory(url, decode_responses=True, max_connections=max_connections,
                            socket_timeout=io_timeout, socket_connect_timeout=io_timeout,
                            retry=Retry(NoBackoff(), 0), retry_on_timeout=False,
                            health_check_interval=0, socket_keepalive=True, protocol=2)
        stack.push_async_callback(close_async, "redis-pool", pool.aclose)
        client = client_factory(connection_pool=pool)
        stack.push_async_callback(close_async, "redis-client", client.aclose)
        yield client  # Lazy connect: Redis outage must not prevent fail-open serving.


@asynccontextmanager
async def owned_aiokafka(producer_factory, consumer_factory):
    """For apps using aiokafka; Confluent consumer has its own serialized owner.

    Application must stop/drain its consume task before leaving this context.
    """
    async with AsyncExitStack() as stack:
        producer = producer_factory()
        stack.push_async_callback(close_async, "kafka-producer", producer.stop, 10.0)
        await producer.start()
        consumer = consumer_factory()
        stack.push_async_callback(close_async, "kafka-consumer", consumer.stop, 10.0)
        await consumer.start()
        yield producer, consumer
