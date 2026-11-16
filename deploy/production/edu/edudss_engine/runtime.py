"""Redis cache-first delivery and globally limited Celery LLM generation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
import random
import time
from typing import Any
from uuid import uuid4

import httpx
import redis
from celery import Celery


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def paragraph_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Scope:
    school_id: str
    course_id: str
    principal_id: str
    student_id: str | None = None

    def __post_init__(self):
        for value in (self.school_id, self.course_id, self.principal_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 255:
                raise ValueError("An authenticated school/course/principal scope is required.")
        if self.student_id is not None and (not isinstance(self.student_id, str) or not self.student_id.strip()):
            raise ValueError("student_id must be an exact nonempty identifier.")


@dataclass(frozen=True)
class RuntimeSettings:
    redis_url: str = field(default="redis://localhost:6379/0", repr=False)
    broker_url: str = field(default="redis://localhost:6379/1", repr=False)
    demo: bool = False
    provider_base_url: str = ""
    provider_model: str = ""
    provider_api_key: str = field(default="", repr=False)
    requests_per_minute: int = 10
    tokens_per_minute: int = 30000
    max_output_tokens: int = 600
    max_prompt_bytes: int = 12000
    provider_timeout: float = 30.0
    cache_ttl: int = 86400
    job_ttl: int = 86400
    queue_deadline: int = 3600
    lease_seconds: int = 120
    max_provider_retries: int = 6
    max_pending: int = 500
    max_advisor_pending: int = 2
    namespace: str = "edudss:engine:v1"

    @classmethod
    def from_env(cls):
        return cls(
            redis_url=os.getenv("EDUDSS_ENGINE_REDIS_URL", cls.redis_url),
            broker_url=os.getenv("EDUDSS_ENGINE_BROKER_URL", cls.broker_url),
            demo=os.getenv("EDUDSS_ENGINE_DEMO", "") == "1",
            provider_base_url=os.getenv("EDUDSS_ENGINE_LLM_BASE_URL", "").rstrip("/"),
            provider_model=os.getenv("EDUDSS_ENGINE_LLM_MODEL", ""),
            provider_api_key=os.getenv("EDUDSS_ENGINE_LLM_API_KEY", ""),
            requests_per_minute=int(os.getenv("EDUDSS_ENGINE_RPM", "10")),
            tokens_per_minute=int(os.getenv("EDUDSS_ENGINE_TPM", "30000")),
            max_pending=int(os.getenv("EDUDSS_ENGINE_MAX_PENDING", "500")),
            max_advisor_pending=int(os.getenv("EDUDSS_ENGINE_MAX_ADVISOR_PENDING", "2")),
        )

    def __post_init__(self):
        if not 1 <= self.requests_per_minute <= 10:
            raise ValueError("The shared free-key request limit must be between 1 and 10 per minute.")
        if not 1 <= self.max_pending <= 500 or not 1 <= self.max_advisor_pending <= 2:
            raise ValueError("Pending limits cannot exceed 500 global and 2 advisory jobs per principal.")
        if self.tokens_per_minute < 0 or min(self.cache_ttl, self.job_ttl, self.queue_deadline, self.lease_seconds) <= 0:
            raise ValueError("Invalid runtime capacity or expiry settings.")

    @property
    def provider_scope(self):
        # Model is deliberately excluded: one provider/API key shares one quota.
        return _digest(["credential", self.provider_api_key, "demo" if self.demo else "real"])

    @property
    def cache_variant(self):
        return _digest([self.provider_base_url.rstrip("/"), self.provider_model, self.demo, "prompt-v1"])


class QueueUnavailable(RuntimeError):
    pass


class QueueCapacity(QueueUnavailable):
    def __init__(self, code="queue_capacity_reached", retry_after=30):
        super().__init__(code)
        self.code, self.retry_after = code, int(retry_after)


class ProviderFailure(RuntimeError):
    def __init__(self, code="provider_failure", *, retryable=False, retry_after=None):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


class DeferredRetry(RuntimeError):
    def __init__(self, delay, code):
        super().__init__(code)
        self.delay, self.code = max(0.05, float(delay)), code


def parse_retry_after(value, *, now=None):
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, date.timestamp() - (time.time() if now is None else now))
        except (ValueError, TypeError, OverflowError):
            return None


def retry_delay(failures, retry_after=None, *, random_value=None):
    jitter = random.random() if random_value is None else random_value
    backoff = min(300.0, 2.0 ** min(failures, 9)) * (0.5 + 0.5 * jitter)
    return max(backoff, float(retry_after or 0))


LIMIT_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1])*1000 + math.floor(tonumber(clock[2])/1000)
local window = tonumber(ARGV[1])
local old = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now-window)
for _, id in ipairs(old) do redis.call('HDEL', KEYS[2], id) end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-window)
local count = redis.call('ZCARD', KEYS[1])
local used = 0
local weights = redis.call('HVALS', KEYS[2])
for _, weight in ipairs(weights) do used = used + tonumber(weight) end
local cost = tonumber(ARGV[3])
local budget = tonumber(ARGV[4])
if budget > 0 and cost > budget then return {-1, 0} end
if count >= tonumber(ARGV[2]) or (budget > 0 and used+cost > budget) then
    local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return {0, math.max(1, tonumber(first[2])+window-now)}
end
redis.call('ZADD', KEYS[1], now, ARGV[5])
redis.call('HSET', KEYS[2], ARGV[5], cost)
redis.call('PEXPIRE', KEYS[1], window+1000)
redis.call('PEXPIRE', KEYS[2], window+1000)
return {1, 0}
"""

RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

SUBMIT_SCRIPT = """
local previous = redis.call('GET', KEYS[1])
if previous then
    local data = redis.call('GET', ARGV[4]..previous)
    if data then
        local old = cjson.decode(data)
        if old.state ~= 'FAILED' or ARGV[10] ~= '1' then return previous end
    end
end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2])/1000000
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now)
if ARGV[9] ~= '1' then
    if redis.call('ZCARD', KEYS[3]) >= tonumber(ARGV[6]) then return 'FULL_GLOBAL' end
    if ARGV[8] == '1' and redis.call('ZCARD', KEYS[4]) >= tonumber(ARGV[7]) then
        return 'FULL_ADVISOR'
    end
    redis.call('ZADD', KEYS[3], ARGV[5], ARGV[1])
    redis.call('EXPIRE', KEYS[3], ARGV[3])
    if ARGV[8] == '1' then
        redis.call('ZADD', KEYS[4], ARGV[5], ARGV[1])
        redis.call('EXPIRE', KEYS[4], ARGV[3])
    end
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return ARGV[1]
"""

FINISH_SCRIPT = """
local previous = redis.call('GET', KEYS[1])
if previous then
    local old = cjson.decode(previous)
    if old.state == 'SUCCEEDED' or old.state == 'FAILED' then
        redis.call('ZREM', KEYS[2], ARGV[3])
        redis.call('ZREM', KEYS[3], ARGV[3])
        return previous
    end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('ZREM', KEYS[2], ARGV[3])
redis.call('ZREM', KEYS[3], ARGV[3])
return ARGV[1]
"""


class GlobalRateLimiter:
    def __init__(self, client, settings):
        self.redis, self.settings = client, settings

    def reserve(self, tokens):
        if type(tokens) is not int or tokens < 1:
            raise ValueError("Token reservation must be a positive integer.")
        prefix = self.settings.namespace + ":quota:" + self.settings.provider_scope
        try:
            result = self.redis.eval(LIMIT_SCRIPT, 2, prefix + ":calls", prefix + ":weights",
                                     60000, self.settings.requests_per_minute, int(tokens),
                                     self.settings.tokens_per_minute, uuid4().hex)
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None
        accepted, wait_ms = int(result[0]), int(result[1])
        if accepted < 0:
            raise ProviderFailure("prompt_exceeds_configured_token_budget")
        if not accepted:
            raise DeferredRetry(wait_ms / 1000.0, "global_provider_quota")
        return True


def call_provider(settings, prompt):
    if settings.demo:
        return "DEMO_ONLY (no LLM call): " + prompt[:350]
    if not all((settings.provider_base_url, settings.provider_model, settings.provider_api_key)):
        raise ProviderFailure("provider_not_configured")
    if not settings.provider_base_url.startswith(("https://", "http://")):
        raise ProviderFailure("provider_configuration_invalid")
    try:
        with httpx.Client(timeout=settings.provider_timeout, follow_redirects=False) as client:
            response = client.post(settings.provider_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.provider_api_key},
                json={"model": settings.provider_model, "temperature": 0.2,
                      "max_tokens": settings.max_output_tokens,
                      "messages": [
                          {"role": "system", "content": "Explain educational material using only the supplied evidence. Do not claim a guaranteed score or diagnosis. Keep the response concise."},
                          {"role": "user", "content": prompt},
                      ]})
    except httpx.TransportError:
        raise ProviderFailure("provider_network_failure", retryable=True) from None
    if response.status_code == 429:
        raise ProviderFailure("provider_rate_limited", retryable=True,
                              retry_after=parse_retry_after(response.headers.get("retry-after")))
    if response.status_code >= 500 or response.status_code == 408:
        raise ProviderFailure("provider_temporarily_unavailable", retryable=True)
    if response.status_code != 200:
        raise ProviderFailure("provider_request_refused")
    try:
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
        return content.strip()
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderFailure("provider_response_invalid") from None


class QueueRuntime:
    """Public submission/cache APIs never invoke a model or provider synchronously."""

    def __init__(self, settings=None, redis_client=None, celery=None):
        self.settings = settings or RuntimeSettings.from_env()
        self._owns_redis = redis_client is None
        self._owns_celery = celery is None
        self.redis = redis_client if redis_client is not None else redis.Redis.from_url(
            self.settings.redis_url, socket_timeout=3, socket_connect_timeout=3,
            decode_responses=True)
        self.celery = celery if celery is not None else make_producer(self.settings)
        self.limiter = GlobalRateLimiter(self.redis, self.settings)

    def close(self):
        try:
            if self._owns_celery:
                self.celery.close()
        finally:
            if self._owns_redis:
                self.redis.close()

    def _key(self, kind, value):
        return self.settings.namespace + ":" + kind + ":" + _digest(value)

    def _job_key(self, job_id):
        return self.settings.namespace + ":job:" + job_id

    def _read(self, key):
        try:
            data = self.redis.get(key)
            value = json.loads(data) if data else None
            return value if isinstance(value, dict) else None
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None
        except (ValueError, TypeError):
            return None

    def _save(self, key, value, ttl):
        try:
            self.redis.set(key, _json(value), ex=ttl)
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None

    def _release(self, key, token):
        try:
            self.redis.eval(RELEASE_SCRIPT, 1, key, token)
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None

    def _paragraph_key(self, scope, lecture_id, version, paragraph_id, text_hash, prompt_version):
        return self._key("paragraph", [scope.school_id, scope.course_id, str(lecture_id),
            str(version), str(paragraph_id), text_hash, prompt_version, self.settings.cache_variant])

    def get_cached_paragraph(self, scope, lecture_id, version, paragraph_id, text_hash,
                             prompt_version="v1"):
        """Shared course cache only. Authorization/enrollment is enforced by the API."""
        return self._read(self._paragraph_key(scope, lecture_id, version, paragraph_id,
                                              text_hash, prompt_version))

    def _public(self, job):
        return {key: job.get(key) for key in (
            "job_id", "state", "kind", "created_at", "updated_at", "result", "error",
            "retry_in_seconds", "demo", "source_version",
        )}

    def get_job(self, scope, job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
            return None
        job = self._read(self._job_key(job_id))
        if job is None or job.get("owner") != asdict(scope):
            return None
        if job.get("state") not in {"SUCCEEDED", "FAILED"} and time.time() >= job["deadline"]:
            self._fail(job, "job_deadline_exceeded")
        return self._public(job)

    def pending_snapshot(self, scope):
        """Trusted admin API only; bounded course metadata, never prompts/results."""
        clock = self.redis.time()
        now = float(clock[0]) + float(clock[1]) / 1000000
        ids = self.redis.zrangebyscore(self.settings.namespace + ":pending", now, "+inf", start=0, num=500)
        raw = self.redis.mget([self._job_key(value.decode() if isinstance(value, bytes) else value) for value in ids]) if ids else []
        rows, counts = [], {}
        for value in raw:
            if value is None:
                continue
            job = json.loads(value)
            owner = job.get("owner", {})
            if owner.get("school_id") != scope.school_id or owner.get("course_id") != scope.course_id:
                continue
            if job["state"] in {"SUCCEEDED", "FAILED"} or job["deadline"] <= now:
                continue
            counts[job["state"]] = counts.get(job["state"], 0) + 1
            rows.append({key: job.get(key) for key in ("job_id", "kind", "state", "created_at",
                                                     "updated_at", "retry_in_seconds", "error")})
        return dict(pending=len(rows), states=counts, jobs=rows,
                    scope="Current unexpired pending LLM jobs for this school/course; not job history.")

    def _pending_keys(self, scope):
        return (self.settings.namespace + ":pending",
                self._key("advisor_pending", [scope.school_id, scope.principal_id]))

    def _finish(self, job, state, *, result=None, error=None):
        job.update(state=state, result=result, error=error, retry_in_seconds=None,
                   updated_at=time.time(), admitted=False)
        try:
            value = self.redis.eval(FINISH_SCRIPT, 3, self._job_key(job["job_id"]),
                                    job["pending_global"], job["pending_principal"],
                                    _json(job), self.settings.job_ttl, job["job_id"])
            final = json.loads(value)
            job.clear()
            job.update(final)
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None

    def _fail(self, job, error):
        self._finish(job, "FAILED", error=error)

    def _submit(self, scope, kind, identity, payload, cache_key, *, retry_failed=False):
        payload_hash = _digest(payload)
        dedup = self._key("dedup", [kind, asdict(scope), identity])
        job_id, now = uuid4().hex, time.time()
        cached = self._read(cache_key)
        pending_global, pending_principal = self._pending_keys(scope)
        job = dict(job_id=job_id, owner=asdict(scope), kind=kind, payload=payload,
                   payload_hash=payload_hash, cache_key=cache_key, dedup_key=dedup,
                   state="SUCCEEDED" if cached is not None else "QUEUED",
                   result=cached, error=None, created_at=now, updated_at=now,
                   deadline=now + self.settings.queue_deadline, failures=0,
                   retry_in_seconds=None, demo=self.settings.demo, source_version=payload.get("source_version"),
                   admitted=cached is None, pending_global=pending_global,
                   pending_principal=pending_principal, cache_variant=self.settings.cache_variant)
        try:
            selected = self.redis.eval(SUBMIT_SCRIPT, 4, dedup, self._job_key(job_id),
                                       pending_global, pending_principal,
                                       job_id, _json(job), self.settings.job_ttl,
                                       self.settings.namespace + ":job:", job["deadline"],
                                       self.settings.max_pending, self.settings.max_advisor_pending,
                                       int(kind == "advisor"), int(cached is not None),
                                       int(bool(retry_failed)))
            if isinstance(selected, bytes):
                selected = selected.decode()
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None
        if selected == "FULL_GLOBAL":
            raise QueueCapacity("global_queue_capacity")
        if selected == "FULL_ADVISOR":
            raise QueueCapacity("principal_advisor_capacity")
        if selected != job_id:
            existing = self._read(self._job_key(selected))
            if existing is None:
                raise QueueUnavailable("job_submission_race")
            if existing["payload_hash"] != payload_hash:
                raise ValueError("An idempotency key cannot be reused for different inputs.")
            if existing["state"] == "SUCCEEDED" and kind == "paragraph" and cached is None and isinstance(existing.get("result"), dict):
                # Explicit instructor resubmission can restore its saved result
                # after cache expiry without making another provider request.
                self._save(cache_key, existing["result"], self.settings.cache_ttl)
            if existing["state"] not in {"SUCCEEDED", "FAILED"} and time.time() >= existing["deadline"]:
                self._fail(existing, "job_deadline_exceeded")
                if retry_failed:
                    return self._submit(scope, kind, identity, payload, cache_key, retry_failed=True)
            return self._public(existing)
        if cached is None:
            try:
                self.celery.send_task("edudss_engine.generate", args=[job_id], queue="llm")
            except Exception:
                self._fail(job, "queue_unavailable")
                raise QueueUnavailable("queue_unavailable") from None
        return self._public(job)

    def submit_paragraph(self, scope, lecture_id, version, paragraph_id, text,
                         prompt_version="v1", retry_failed=False):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("A nonempty paragraph is required.")
        prompt = "Explain this lecture paragraph in clear language. Preserve its scope.\n" + text
        self._check_prompt(prompt)
        text_hash = paragraph_hash(text)
        cache = self._paragraph_key(scope, lecture_id, version, paragraph_id, text_hash, prompt_version)
        payload = dict(prompt=prompt, lecture_id=str(lecture_id), version=str(version),
                       paragraph_id=str(paragraph_id), text_hash=text_hash,
                       prompt_version=prompt_version)
        return self._submit(scope, "paragraph", [str(lecture_id), str(version), str(paragraph_id),
                             text_hash, prompt_version, self.settings.cache_variant], payload, cache, retry_failed=retry_failed)

    def submit_advisor(self, scope, context, idempotency_key, source_version=None):
        if scope.student_id is None:
            raise ValueError("Advisory generation requires an authenticated student scope.")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("An idempotency key between 1 and 128 characters is required.")
        if not isinstance(context, dict):
            raise ValueError("Advisory context must be structured JSON.")
        compact = _json(context)
        prompt = ("Write concise learning advice only from these server facts. Legend: "
                  "g=grade estimate trained on 0..10 scale, may extrapolate beyond it; r=predicted risk category 0 normal,1 attention,2 higher risk "
                  "(not a probability or diagnosis); a=ordered groups [topic,item ID,...] of "
                  "prerequisite-eligible exercises in the fitted approved catalog, preserving order; "
                  "w=observed weak topics; src=data source (synthetic means demonstration only); at=data reference timestamp; stale=true means historical observations, not a current student diagnosis. Missing data stays unknown. No causal or guaranteed-outcome claims.\n") + compact
        self._check_prompt(prompt)
        payload = dict(prompt=prompt, context_hash=_digest(context), source_version=source_version)
        cache = self._key("advice", [asdict(scope), _digest(context), source_version, self.settings.cache_variant])
        return self._submit(scope, "advisor", idempotency_key, payload, cache)

    def _check_prompt(self, prompt):
        if len(prompt.encode("utf-8")) > self.settings.max_prompt_bytes:
            raise ValueError("Prompt exceeds the configured input byte limit.")

    def execute_job(self, job_id):
        """Worker-only operation. Retriable waits raise DeferredRetry, never sleep."""
        job = self._read(self._job_key(job_id))
        if job is None or job.get("state") in {"SUCCEEDED", "FAILED"}:
            return None if job is None else self._public(job)
        if time.time() >= job["deadline"]:
            self._fail(job, "job_deadline_exceeded")
            return self._public(job)
        if job.get("cache_variant", self.settings.cache_variant) != self.settings.cache_variant:
            self._fail(job, "provider_configuration_changed")
            return self._public(job)
        cached = self._read(job["cache_key"])
        if cached is not None:
            self._finish(job, "SUCCEEDED", result=cached)
            return self._public(job)
        lease_key = self._key("lease", job["cache_key"])
        lease = uuid4().hex
        try:
            acquired = self.redis.set(lease_key, lease, nx=True, ex=self.settings.lease_seconds)
        except redis.RedisError:
            raise QueueUnavailable("runtime_storage_unavailable") from None
        if not acquired:
            raise DeferredRetry(2, "generation_already_running")
        try:
            cached = self._read(job["cache_key"])
            if cached is not None:
                self._finish(job, "SUCCEEDED", result=cached)
                return self._public(job)
            job.update(state="STARTED", updated_at=time.time(), error=None, retry_in_seconds=None)
            self._save(self._job_key(job_id), job, self.settings.job_ttl)
            # Conservative reservation, not an exact tokenizer claim.
            cost = len(job["payload"]["prompt"].encode("utf-8")) + self.settings.max_output_tokens + 512
            try:
                self.limiter.reserve(cost)
                answer = call_provider(self.settings, job["payload"]["prompt"])
            except DeferredRetry as deferred:
                job.update(state="RETRY", retry_in_seconds=deferred.delay, updated_at=time.time())
                self._save(self._job_key(job_id), job, self.settings.job_ttl)
                raise
            except ProviderFailure as error:
                failures = int(job.get("failures", 0)) + 1
                job["failures"] = failures
                if not error.retryable or failures > self.settings.max_provider_retries:
                    self._fail(job, error.code)
                    return self._public(job)
                delay = retry_delay(failures, error.retry_after)
                if time.time() + delay >= job["deadline"]:
                    self._fail(job, "provider_retry_after_deadline")
                    return self._public(job)
                job.update(state="RETRY", error=error.code, retry_in_seconds=delay,
                           updated_at=time.time())
                self._save(self._job_key(job_id), job, self.settings.job_ttl)
                raise DeferredRetry(delay, error.code) from None
            result = {key: value for key, value in job["payload"].items() if key != "prompt"}
            result.update(text=answer, demo=self.settings.demo,
                          model=self.settings.provider_model or "explicit-demo",
                          generated_at=datetime.now(timezone.utc).isoformat())
            self._save(job["cache_key"], result, self.settings.cache_ttl)
            self._finish(job, "SUCCEEDED", result=result)
            return self._public(job)
        except (DeferredRetry, QueueUnavailable):
            raise
        except Exception:
            # Unexpected adapter errors must not leave a misleading STARTED job
            # or expose provider diagnostics/credentials through task status.
            self._fail(job, "generation_failed")
            return self._public(job)
        finally:
            self._release(lease_key, lease)


_settings = RuntimeSettings.from_env()
celery_app = Celery("edudss_engine", broker=_settings.broker_url)
_CELERY_COMMON = dict(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    worker_max_tasks_per_child=int(os.getenv("CELERY_MAX_TASKS_PER_CHILD", "50")),
    worker_max_memory_per_child=int(os.getenv("CELERY_MAX_MEMORY_PER_CHILD_KIB", "786432")),
    task_routes={"edudss_engine.generate": {"queue": "llm"}},
    broker_transport_options={"visibility_timeout": 3600},
    timezone="UTC", enable_utc=True,
)

if min(_CELERY_COMMON["worker_max_tasks_per_child"], _CELERY_COMMON["worker_max_memory_per_child"]) < 1:
    raise ValueError("Celery process recycle limits must be positive")
celery_app.conf.update(**_CELERY_COMMON)


def make_producer(settings):
    app = Celery("edudss_producer", broker=settings.broker_url)
    app.conf.update(**_CELERY_COMMON)
    app.conf.broker_url = settings.broker_url
    return app


_worker_runtime = None


def close_worker_runtime(**kwargs):
    global _worker_runtime
    runtime, _worker_runtime = _worker_runtime, None
    if runtime is not None:
        runtime.close()


# Close parent-owned sockets BEFORE fork; recycle/shutdown closes child resources.
from celery.signals import worker_before_create_process, worker_process_shutdown
worker_before_create_process.connect(close_worker_runtime, weak=False)
worker_process_shutdown.connect(close_worker_runtime, weak=False)


def worker_runtime():
    global _worker_runtime
    if _worker_runtime is None:
        _worker_runtime = QueueRuntime()
    return _worker_runtime


@celery_app.task(bind=True, name="edudss_engine.generate", rate_limit="10/m",
                 ignore_result=True, acks_late=True, soft_time_limit=90, time_limit=120)
def generate(self, job_id):
    try:
        worker_runtime().execute_job(job_id)
        return None
    except DeferredRetry as deferred:
        raise self.retry(countdown=deferred.delay, exc=RuntimeError(deferred.code), max_retries=None)
    except QueueUnavailable:
        # Redis outages are bounded by the persisted job deadline once recovered.
        raise self.retry(countdown=min(60, 2 ** min(self.request.retries + 1, 6)),
                         exc=RuntimeError("runtime_storage_unavailable"), max_retries=20)
