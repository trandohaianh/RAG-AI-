"""FastAPI boundary: signed identity, live enrollment, cache-only lecture reads."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from typing import Annotated, Literal
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from redis import Redis
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from .auth import Principal, signing_key as validate_key, verify_token
from .runtime import QueueRuntime, QueueUnavailable, RuntimeSettings, Scope, paragraph_hash
from .service import EngineService
from .storage import Repository

ID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemberInput(Input):
    principal_id: ID
    role: Literal["instructor", "student"]
    student_id: ID | None = None
    active: bool = True

    @model_validator(mode="after")
    def student_binding(self):
        if self.role == "student" and self.student_id is None:
            raise ValueError("Student enrollment needs an explicit student_id.")
        return self


class GraphInput(Input):
    edges: list[tuple[ID, ID]] = Field(max_length=5000)


class DatasetInput(Input):
    source: Literal["synthetic", "institutional"]
    as_of: AwareDatetime
    events: list[dict] = Field(min_length=12, max_length=20000)
    classification: list[dict] = Field(min_length=30, max_length=20000)
    items: list[dict] = Field(min_length=1, max_length=10000)
    ratings: list[dict] = Field(default_factory=list, max_length=100000)
    profiles: dict[str, dict] = Field(default_factory=dict)

    @model_validator(mode="after")
    def observation_times(self):
        if self.as_of > datetime.now(timezone.utc):
            raise ValueError("The dataset cutoff cannot be in the future.")
        for profile in self.profiles.values():
            if "observed_at" not in profile:
                raise ValueError("Profiles require observed_at provenance.")
            observed = datetime.fromisoformat(str(profile["observed_at"]).replace("Z", "+00:00"))
            if observed.tzinfo is None or observed > self.as_of:
                raise ValueError("Profile observations must be known at the dataset cutoff.")
        return self


class TrainingInput(Input):
    dataset_id: ID
    request_id: ID = Field(default_factory=lambda: uuid.uuid4().hex)


class AdviceInput(Input):
    request_id: ID
    k: int = Field(default=5, ge=1, le=20)
    interests: str | None = Field(default=None, max_length=1000)


class ParagraphInput(Input):
    paragraph_id: ID
    text: str = Field(min_length=1, max_length=4000)


class LectureInput(Input):
    lecture_id: ID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    paragraphs: list[ParagraphInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_unique_paragraphs(self):
        ids = [item.paragraph_id for item in self.paragraphs]
        if len(set(ids)) != len(ids):
            raise ValueError("Paragraph identifiers must be unique.")
        if any(not item.text.strip() for item in self.paragraphs):
            raise ValueError("Paragraph text must not be blank.")
        if any(len(item.text.encode()) > 10000 for item in self.paragraphs):
            raise ValueError("Split paragraphs into at most 10000 UTF-8 bytes each.")
        return self


class ApprovalInput(Input):
    approved: bool
    version: ID


async def body(request, schema):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 5 * 1024 * 1024:
            raise HTTPException(413, "Request body exceeds 5 MiB.")
        chunks.append(chunk)
    try:
        return schema.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False, include_context=False, include_url=False)) from None


def create_app(redis_client=None, queue_runtime=None, service=None, signing_key=None):
    key = validate_key(signing_key)
    settings = RuntimeSettings.from_env()
    owns_client = redis_client is None
    owns_queue = queue_runtime is None
    client = redis_client if redis_client is not None else Redis.from_url(settings.redis_url, decode_responses=True,
        socket_timeout=3, socket_connect_timeout=3)
    queue = None
    try:
        queue = queue_runtime if queue_runtime is not None else QueueRuntime(settings, redis_client=client)
        repo = Repository(client, key)
        engine = service or EngineService(repo, queue)
    except BaseException:
        try:
            if owns_queue and queue is not None:
                queue.close()
        finally:
            if owns_client:
                client.close()
        raise
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            try:
                if owns_queue:
                    await run_in_threadpool(queue.close)
            finally:
                if owns_client:
                    await run_in_threadpool(client.close)
    app = FastAPI(title="EduDSS Learning Engine", version="0.1.0", lifespan=lifespan)
    app.state.repo, app.state.queue, app.state.service = repo, queue, engine

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse(status_code=403, content={"detail": "This role or course membership is not authorized."})

    @app.exception_handler(LookupError)
    async def absent(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Resource unavailable."})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)[:300]})

    @app.exception_handler(QueueUnavailable)
    async def queue_down(request, exc):
        code = 429 if type(exc).__name__ == "QueueCapacity" else 503
        return JSONResponse(status_code=code, content={"detail": "Generation queue capacity or availability prevents this request."})

    @app.exception_handler(RedisError)
    async def redis_down(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Learning storage is unavailable."})

    async def authorize(request, course, roles=None):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
        try:
            actor = verify_token(header[7:], key)
        except Exception:
            raise HTTPException(401, "Invalid or expired identity.", headers={"WWW-Authenticate": "Bearer"}) from None
        if roles is not None and actor.role not in roles:
            raise PermissionError("Role unavailable.")
        await run_in_threadpool(repo.member, actor, course)
        return actor

    def scope(actor, course):
        return Scope(actor.school_id, course, actor.principal_id, actor.student_id)

    @app.get("/health")
    def health():
        return {"status": "alive"}

    @app.get("/ready")
    def ready():
        try:
            client.ping()
            return {"status": "ready", "demo": settings.demo}
        except RedisError:
            raise HTTPException(503, "Learning storage is unavailable.") from None

    @app.post("/v1/courses/{course}/members")
    async def members(course: str, request: Request):
        actor = await authorize(request, course, {"admin"})
        value = await body(request, MemberInput)
        return await run_in_threadpool(repo.set_member, actor, course, **value.model_dump())

    @app.put("/v1/courses/{course}/knowledge")
    async def knowledge(course: str, request: Request):
        actor = await authorize(request, course, {"admin"})
        value = await body(request, GraphInput)
        return await run_in_threadpool(repo.set_graph, actor.school_id, course, value.edges)

    @app.post("/v1/courses/{course}/datasets", status_code=201)
    async def dataset(course: str, request: Request):
        actor = await authorize(request, course, {"admin", "instructor"})
        value = await body(request, DatasetInput)
        if value.source == "synthetic" and not settings.demo:
            raise HTTPException(422, "Synthetic data require explicit EDUDSS_ENGINE_DEMO=1.")
        return await run_in_threadpool(repo.put_dataset, actor.school_id, course, value.model_dump(mode="json"))

    @app.post("/v1/courses/{course}/training", status_code=202)
    async def training(course: str, request: Request):
        actor = await authorize(request, course, {"admin", "instructor"})
        value = await body(request, TrainingInput)
        return await run_in_threadpool(engine.request_training, actor, course, value.dataset_id, value.request_id)

    @app.get("/v1/courses/{course}/jobs/{job_id}")
    async def job(course: str, job_id: str, request: Request):
        actor = await authorize(request, course)
        result = None
        if actor.role in {"admin", "instructor"}:
            result = await run_in_threadpool(engine.training_status, actor, course, job_id)
        if result is None:
            result = await run_in_threadpool(queue.get_job, scope(actor, course), job_id)
        if result is None:
            raise HTTPException(404, "Job unavailable.")
        return result

    @app.get("/v1/courses/{course}/leaderboard")
    async def leaderboard(course: str, request: Request):
        actor = await authorize(request, course, {"admin", "instructor"})
        return await run_in_threadpool(repo.report, actor.school_id, course)

    @app.get("/v1/courses/{course}/class-risk")
    async def class_risk(course: str, request: Request, offset: int = Query(default=0, ge=0),
                         limit: int = Query(default=50, ge=1, le=100)):
        actor = await authorize(request, course, {"admin", "instructor"})
        return await run_in_threadpool(engine.class_risk, actor, course, offset, limit)

    @app.get("/v1/courses/{course}/queue")
    async def queue_status(course: str, request: Request):
        actor = await authorize(request, course, {"admin"})
        snapshot = await run_in_threadpool(queue.pending_snapshot, scope(actor, course))
        latest = await run_in_threadpool(client.get, repo.key("training_request", actor.school_id, course))
        if isinstance(latest, bytes):
            latest = latest.decode()
        training = await run_in_threadpool(repo.get, "training_job", actor.school_id, course, latest) if latest else None
        snapshot["latest_training"] = ({key: training.get(key) for key in
            ("job_id", "kind", "state", "created_at", "updated_at", "error")} if training else None)
        return snapshot

    @app.get("/v1/courses/{course}/predictions")
    async def predictions(course: str, request: Request):
        actor = await authorize(request, course, {"student"})
        return await run_in_threadpool(engine.predictions, actor, course)

    @app.get("/v1/courses/{course}/recommendations")
    async def recommendations(course: str, request: Request, k: int = Query(default=5, ge=1, le=20),
                              interests: str | None = Query(default=None, max_length=1000)):
        actor = await authorize(request, course, {"student"})
        return await run_in_threadpool(engine.recommendations, actor, course, k, interests)

    @app.post("/v1/courses/{course}/advice", status_code=202)
    async def advice(course: str, request: Request):
        actor = await authorize(request, course, {"student"})
        value = await body(request, AdviceInput)
        return await run_in_threadpool(engine.advice, actor, course, value.request_id, value.k, value.interests)

    async def enqueue_paragraphs(actor, course, lecture, retry_failed=False):
        jobs = []
        for paragraph in lecture["paragraphs"]:
            kwargs = {"retry_failed": True} if retry_failed else {}
            result = await run_in_threadpool(queue.submit_paragraph, scope(actor, course),
                lecture["lecture_id"], lecture["version"], paragraph["paragraph_id"], paragraph["text"], **kwargs)
            jobs.append(result)
        return dict(lecture_id=lecture["lecture_id"], version=lecture["version"],
                    approved=lecture["approved"], jobs=jobs)

    @app.post("/v1/courses/{course}/lectures", status_code=202)
    async def upload(course: str, request: Request):
        actor = await authorize(request, course, {"admin", "instructor"})
        value = await body(request, LectureInput)
        paragraphs = [dict(paragraph_id=item.paragraph_id, text=item.text,
                           text_hash=paragraph_hash(item.text)) for item in value.paragraphs]
        lecture = await run_in_threadpool(repo.put_lecture, actor.school_id, course,
            value.lecture_id, value.title, paragraphs)
        return await enqueue_paragraphs(actor, course, lecture)

    @app.post("/v1/courses/{course}/lectures/{lecture_id}/retry", status_code=202)
    async def retry_lecture(course: str, lecture_id: str, request: Request):
        actor = await authorize(request, course, {"admin", "instructor"})
        lecture = await run_in_threadpool(repo.get, "lecture", actor.school_id, course, lecture_id)
        if not lecture:
            raise HTTPException(404, "Lecture unavailable.")
        return await enqueue_paragraphs(actor, course, lecture, retry_failed=True)

    @app.put("/v1/courses/{course}/lectures/{lecture_id}/approval")
    async def approve(course: str, lecture_id: str, request: Request):
        actor = await authorize(request, course, {"admin", "instructor"})
        value = await body(request, ApprovalInput)
        result = await run_in_threadpool(repo.approve_lecture, actor.school_id, course,
            lecture_id, value.version, value.approved)
        if result == "MISSING":
            raise HTTPException(404, "Lecture unavailable.")
        if result == "CONFLICT":
            raise HTTPException(409, "Lecture content changed; review the current version first.")
        return result

    @app.get("/v1/courses/{course}/lectures/{lecture_id}/paragraphs/{paragraph_id}")
    async def read(course: str, lecture_id: str, paragraph_id: str, request: Request):
        actor = await authorize(request, course)
        lecture = await run_in_threadpool(repo.get, "lecture", actor.school_id, course, lecture_id)
        if not lecture or not lecture["approved"]:
            raise HTTPException(404, "Approved lecture unavailable.")
        paragraph = next((part for part in lecture["paragraphs"] if part["paragraph_id"] == paragraph_id), None)
        if paragraph is None:
            raise HTTPException(404, "Paragraph unavailable.")
        cached = await run_in_threadpool(queue.get_cached_paragraph, scope(actor, course),
            lecture_id, lecture["version"], paragraph_id, paragraph["text_hash"])
        if cached is None:
            return JSONResponse(status_code=202, content=dict(state="PENDING", lecture_id=lecture_id,
                version=lecture["version"], paragraph_id=paragraph_id, original_text=paragraph["text"]))
        return dict(state="READY", lecture_id=lecture_id, version=lecture["version"],
                    paragraph_id=paragraph_id, original_text=paragraph["text"], explanation=cached)
    def openapi_schema():
        if app.openapi_schema is not None:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi
        result = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = result.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        components.setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        bodies = {
            ("/v1/courses/{course}/members", "post"): MemberInput,
            ("/v1/courses/{course}/knowledge", "put"): GraphInput,
            ("/v1/courses/{course}/datasets", "post"): DatasetInput,
            ("/v1/courses/{course}/training", "post"): TrainingInput,
            ("/v1/courses/{course}/advice", "post"): AdviceInput,
            ("/v1/courses/{course}/lectures", "post"): LectureInput,
            ("/v1/courses/{course}/lectures/{lecture_id}/approval", "put"): ApprovalInput,
        }
        for (path, method), model in bodies.items():
            schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            schemas.update(schema.pop("$defs", {}))
            schemas[model.__name__] = schema
            result["paths"][path][method]["requestBody"] = {
                "required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/" + model.__name__}}}}
        for path, operations in result["paths"].items():
            if path.startswith("/v1/"):
                for operation in operations.values():
                    if isinstance(operation, dict):
                        operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = result
        return result
    app.openapi = openapi_schema
    return app
