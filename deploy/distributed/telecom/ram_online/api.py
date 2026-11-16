"""Authenticated production boundary for the RAM-first online policy gate."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
import hmac
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from .core import OnlineGate, OnlineUnavailable, PolicyStore
from .production import FailOpenGate
from .resources import owned_redis


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    phone_number: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$", max_length=16)


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        key = os.environ.get("TELECOM_API_KEY", "")
        if len(key) < 32:
            raise RuntimeError("A unique TELECOM_API_KEY is required")
        app.state.api_key = key
        url = os.environ["POLICY_REDIS_URL"]
        async with owned_redis(url) as online, owned_redis(url, io_timeout=1.0, max_connections=2) as builder:
            raw = OnlineGate(PolicyStore(online), budget_ms=5.0)
            app.state.gate = FailOpenGate(raw)
            async def refresh():
                while True:
                    try:
                        async with asyncio.timeout(10):
                            await raw.refresh_snapshot(builder)
                    except (RedisError, OnlineUnavailable, TimeoutError, ValueError):
                        pass  # Keep prior snapshot. Redis version guard still applies.
                    await asyncio.sleep(30)
            task = asyncio.create_task(refresh(), name="policy-bloom-refresh")
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Telecom Online Policy", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        # An optional Redis outage must not make the fail-open API stop serving.
        return {"status": "serving", "circuit": app.state.gate.breaker.state}

    @app.post("/v1/check-call")
    async def check(body: CallIn, x_api_key: str = Header(default="")):
        if not hmac.compare_digest(x_api_key.encode(), app.state.api_key.encode()):
            raise HTTPException(401, "Authentication required")
        decision = await app.state.gate.check_call_allowed(body.phone_number)
        return asdict(decision)
    return app


app = create_app()
