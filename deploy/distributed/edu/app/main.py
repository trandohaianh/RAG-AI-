"""Production composition: existing engine + async cache delivery + presentation APIs."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import re
import time
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from redis import asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from starlette.concurrency import run_in_threadpool
from edudss_engine.api import create_app as create_engine_app, body
from edudss_engine.auth import signing_key, verify_token
from edudss_engine.runtime import RuntimeSettings, paragraph_hash

class CMSInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lecture_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=300000)

def split_cms(text, maximum=4000):
    """Whitespace-aware bounded chunks, offline ONLY. No provider call on upload."""
    if not text.strip() or len(text.encode()) > 400000:
        raise ValueError("Lecture must be nonblank and <=400000 UTF-8 bytes")
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        rest = paragraph.strip()
        while rest:
            end = min(len(rest), maximum)
            if end < len(rest):
                boundary = rest.rfind(" ", 0, end)
                if boundary > maximum//2: end = boundary
            piece, rest = rest[:end].strip(), rest[end:].strip()
            # Existing queue caps paragraph UTF-8 bytes at 10000.
            if len(piece.encode()) > 10000:
                end = max(1, len(piece)//2)
                rest = piece[end:] + (" " + rest if rest else "")
                piece = piece[:end]
            if piece: pieces.append(piece)
            if len(pieces) > 100: raise ValueError("Split the lecture into <=100 paragraphs per upload")
    return [dict(paragraph_id=f"p{i+1}", text=t, text_hash=paragraph_hash(t)) for i,t in enumerate(pieces)]

def create_app(**dependencies):
    app = create_engine_app(**dependencies)
    repo, queue = app.state.repo, app.state.queue
    key = signing_key(dependencies.get("signing_key"))
    settings = RuntimeSettings.from_env()
    engine_lifespan = app.router.lifespan_context
    app.state.worker_inspections = 0

    @asynccontextmanager
    async def lifespan(instance):
        async with engine_lifespan(instance):
            pool = aioredis.ConnectionPool.from_url(settings.redis_url, max_connections=64,
                decode_responses=True, socket_connect_timeout=.05, socket_timeout=.05,
                retry=Retry(NoBackoff(), 0), health_check_interval=0)
            cache = aioredis.Redis(connection_pool=pool)
            instance.state.delivery = cache
            try: yield
            finally:
                try: await cache.aclose()
                finally: await pool.aclose()
    app.router.lifespan_context = lifespan

    def identity(request, roles=None):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "): raise HTTPException(401, "Authentication required")
        try: actor = verify_token(header[7:], key)
        except Exception: raise HTTPException(401, "Invalid or expired identity") from None
        if roles is not None and actor.role not in roles: raise HTTPException(403, "Role unavailable")
        return actor

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/v1/"): response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/v1/me")
    async def me(request: Request):
        return asdict(identity(request))

    @app.get("/v1/courses/{course}/knowledge")
    async def graph(course: str, request: Request):
        actor = identity(request, {"admin"})
        await run_in_threadpool(repo.member, actor, course)
        return await run_in_threadpool(repo.graph, actor.school_id, course)

    @app.get("/v1/admin/workers")
    async def workers(request: Request):
        identity(request, {"admin"})
        # Prevent timed-out inspections from accumulating worker threads indefinitely.
        if app.state.worker_inspections >= 2: raise HTTPException(429, "Inspection already running")
        app.state.worker_inspections += 1
        def inspect():
            try:
                stats = queue.celery.control.inspect(timeout=1.0).stats()
                return dict(state="READY" if stats else "UNKNOWN", responding_workers=len(stats or {}))
            except Exception: return dict(state="UNKNOWN", responding_workers=None)
            finally: app.state.worker_inspections -= 1
        return await run_in_threadpool(inspect)

    @app.post("/v1/courses/{course}/cms", status_code=202)
    async def cms(course: str, request: Request):
        actor = identity(request, {"admin", "instructor"})
        await run_in_threadpool(repo.member, actor, course)
        value = await body(request, CMSInput)
        paragraphs = split_cms(value.text)
        lecture = await run_in_threadpool(repo.put_lecture, actor.school_id, course,
            value.lecture_id, value.title, paragraphs)
        from edudss_engine.runtime import Scope
        jobs = []
        for paragraph in lecture["paragraphs"]:
            result = await run_in_threadpool(queue.submit_paragraph,
                Scope(actor.school_id, course, actor.principal_id, actor.student_id),
                value.lecture_id, lecture["version"], paragraph["paragraph_id"], paragraph["text"])
            jobs.append(result)
        return dict(lecture_id=value.lecture_id, version=lecture["version"], approved=lecture["approved"],
            paragraphs=[p["paragraph_id"] for p in paragraphs], jobs=jobs)

    # Replace the sync-threadpool cache reader, preserving URL and authorization semantics.
    path = "/v1/courses/{course}/lectures/{lecture_id}/paragraphs/{paragraph_id}"
    app.router.routes[:] = [r for r in app.router.routes if getattr(r,"path",None) != path]
    @app.get(path)
    async def read(course: str, lecture_id: str, paragraph_id: str, request: Request):
        started = time.perf_counter()
        actor = identity(request)
        if not course.strip() or len(course)>128: raise HTTPException(422,"Explicit course required")
        client = app.state.delivery
        try:
            async with asyncio.timeout(.1):
                # One round trip for membership + current approved lecture metadata.
                async with client.pipeline(transaction=False) as pipeline:
                    pipeline.get(repo.key("member", actor.school_id, course, actor.principal_id))
                    pipeline.get(repo.key("lecture", actor.school_id, course, lecture_id))
                    member_raw, lecture_raw = await pipeline.execute()
                member = json.loads(member_raw) if member_raw else None
                if actor.role != "admin" and (not member or not member.get("active") or member.get("role") != actor.role or
                    (actor.role == "student" and member.get("student_id") != actor.student_id)):
                    raise HTTPException(403,"Current course membership required")
                lecture = json.loads(lecture_raw) if lecture_raw else None
                if not lecture or not lecture.get("approved"): raise HTTPException(404,"Approved lecture unavailable")
                paragraph = next((p for p in lecture["paragraphs"] if p["paragraph_id"]==paragraph_id),None)
                if paragraph is None: raise HTTPException(404,"Paragraph unavailable")
                from edudss_engine.runtime import Scope
                cache_key = queue._paragraph_key(Scope(actor.school_id,course,actor.principal_id,actor.student_id),
                    lecture_id,lecture["version"],paragraph_id,paragraph["text_hash"],"v1")
                raw = await client.get(cache_key)
        except TimeoutError:
            raise HTTPException(503,"Lecture cache deadline exceeded") from None
        cached = json.loads(raw) if raw else None
        value = dict(state="READY" if cached is not None else "PENDING",lecture_id=lecture_id,
            version=lecture["version"],paragraph_id=paragraph_id,original_text=paragraph["text"])
        if cached is not None: value["explanation"] = cached
        return JSONResponse(status_code=200 if cached is not None else 202,content=value,
            headers={"Server-Timing": f"delivery;dur={(time.perf_counter()-started)*1000:.3f}"})
    app.openapi_schema = None
    return app
