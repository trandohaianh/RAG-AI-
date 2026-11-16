"""Background training, immutable serving bundles and deterministic student facts."""
from __future__ import annotations
from collections import OrderedDict
from datetime import datetime, timezone
import threading
import uuid

import pandas as pd

from .auth import Principal
from .features import REGRESSION_FEATURES, build_prediction_features, build_regression_dataset
from .recommendation import HybridRecommender, evaluate_temporal
from .runtime import Scope, QueueUnavailable, celery_app
from .storage import Repository, digest, dumps, utcnow
from .validation import train_classification, train_regression

TRAINING_TTL = 86400


class EngineService:
    def __init__(self, repository, queue_runtime, celery=None):
        self.repo, self.queue = repository, queue_runtime
        self.celery = celery if celery is not None else queue_runtime.celery
        self._bundles = OrderedDict()
        self._bundle_lock = threading.Lock()

    def request_training(self, actor, course, dataset_id, request_id):
        if actor.role not in {"admin", "instructor"}:
            raise PermissionError("Training requires an instructor or administrator.")
        self.repo.member(actor, course)
        record = self.repo.dataset(actor.school_id, course, dataset_id)
        identity = (actor.school_id, course, actor.principal_id, request_id)
        job_id = uuid.uuid4().hex
        payload_hash = digest([dataset_id, self.repo.graph(actor.school_id, course)["version"]])
        row = dict(job_id=job_id, owner=actor.principal_id, role=actor.role,
            dataset_id=record["id"], state="QUEUED", kind="training", created_at=utcnow(),
            updated_at=utcnow(), result=None, error=None)
        script = """
        local old = redis.call('GET',KEYS[1])
        if old then
            local data = cjson.decode(old)
            if data.hash ~= ARGV[1] then return {'CONFLICT'} end
            return {data.job_id}
        end
        redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[4])
        redis.call('SET',KEYS[2],ARGV[3],'EX',ARGV[4])
        redis.call('SET',KEYS[3],ARGV[5])
        return {ARGV[5],'NEW'}
        """
        result = self.repo.redis.eval(script, 3,
            self.repo.key("training_idem", *identity),
            self.repo.key("training_job", actor.school_id, course, job_id),
            self.repo.key("training_request", actor.school_id, course),
            payload_hash, dumps(dict(hash=payload_hash, job_id=job_id)), dumps(row), TRAINING_TTL, job_id)
        result = [value.decode() if isinstance(value, bytes) else value for value in result]
        if result[0] == "CONFLICT":
            raise ValueError("This request ID already refers to different training inputs.")
        if len(result) > 1:
            try:
                self.celery.send_task("edudss_engine.train", args=[actor.school_id, course, job_id], queue="cpu")
            except Exception:
                row.update(state="FAILED", error="queue_unavailable", updated_at=utcnow())
                self.repo.put("training_job", (actor.school_id, course, job_id), row, TRAINING_TTL)
                raise QueueUnavailable("The training queue is unavailable.") from None
        return self.repo.training_job(actor, course, result[0])

    def training_status(self, actor, course, job_id):
        if actor.role not in {"admin", "instructor"}:
            raise PermissionError("Training status requires an instructor or administrator.")
        self.repo.member(actor, course)
        row = self.repo.training_job(actor, course, job_id)
        if row and row["state"] in {"QUEUED", "STARTED"}:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
            if age > 1800:
                row.update(state="FAILED", error="training_deadline")
        return row

    def run_training(self, school, course, job_id):
        row = self.repo.get("training_job", school, course, job_id)
        if not row or row["state"] in {"SUCCEEDED", "FAILED", "SUPERSEDED"}:
            return
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
        if age > 1800:
            row.update(state="FAILED", error="training_deadline", updated_at=utcnow())
            self.repo.put("training_job", (school, course, job_id), row, TRAINING_TTL)
            return
        actor = Principal(school, row["owner"], row["role"])
        row.update(state="STARTED", updated_at=utcnow())
        self.repo.put("training_job", (school, course, job_id), row, TRAINING_TTL)
        try:
            self.repo.member(actor, course)
            dataset = self.repo.dataset(school, course, row["dataset_id"])["payload"]
            graph = self.repo.graph(school, course)
            cutoff = pd.Timestamp(dataset["as_of"])
            events = pd.DataFrame(dataset["events"])
            regression_frame = build_regression_dataset(events)
            classification_frame = pd.DataFrame(dataset["classification"])
            columns = sorted(column for column in classification_frame if column.startswith("interaction_"))
            regression = train_regression(regression_frame, as_of=cutoff)
            classification = train_classification(classification_frame, columns, as_of=cutoff)
            items = pd.DataFrame(dataset["items"])
            ratings = pd.DataFrame(dataset["ratings"], columns=["student_id", "item_id", "rating", "timestamp", "school_id", "course_id"])
            recommender = HybridRecommender().fit(items, ratings, graph["edges"],
                school_id=school, course_id=course, cutoff=cutoff)
            # Recommendation evaluation uses an earlier cutoff and unknown mastery.
            # Final-time mastery is not allowed to leak into an earlier evaluation.
            rating_times = pd.to_datetime(ratings["timestamp"], utc=True)
            evaluation_ratings = ratings[rating_times <= cutoff].copy()
            times = rating_times[rating_times <= cutoff].sort_values()
            rec_report = None
            if len(times) >= 10 and times.nunique() > 1:
                rec_cutoff = times.iloc[max(0, int(len(times) * 0.8) - 1)]
                rec_report = evaluate_temporal(items, evaluation_ratings, graph["edges"],
                    school_id=school, course_id=course, cutoff=rec_cutoff, k=5)
            snapshots = build_prediction_features(events, cutoff)
            regression_snapshots = {}
            for snapshot in snapshots.to_dict("records"):
                student = snapshot.pop("student_id")
                clean = {key: (None if pd.isna(value) else value.isoformat() if hasattr(value, "isoformat") else value)
                         for key, value in snapshot.items()}
                regression_snapshots[student] = clean
            bundle = dict(regression=regression.model, classification=classification.model,
                recommendation=recommender, regression_features=list(REGRESSION_FEATURES),
                classification_features=columns, regression_snapshots=regression_snapshots,
                profiles=dataset["profiles"], source=dataset["source"], as_of=dataset["as_of"],
                dataset_id=row["dataset_id"], graph_version=graph["version"])
            report = dict(regression=regression.to_dict(), classification=classification.to_dict(),
                recommendation=rec_report, source=dataset["source"], as_of=dataset["as_of"],
                dataset_id=row["dataset_id"], graph_version=graph["version"])
            self.repo.member(actor, course)
            if self.repo.graph(school, course)["version"] != graph["version"]:
                raise ValueError("Knowledge prerequisites changed during training.")
            version = self.repo.save_artifact(school, course, bundle, report)
            active = self.repo.activate(school, course, version, requested_job=job_id)
            row.update(state="SUCCEEDED" if active else "SUPERSEDED",
                       result=dict(version=version, active=active, report=report), error=None)
        except Exception as exc:
            # Never put raw student records, connection errors or provider secrets in status.
            row.update(state="FAILED", result=None,
                error="invalid_training_data" if isinstance(exc, (ValueError, KeyError, TypeError)) else "training_failed")
        row["updated_at"] = utcnow()
        self.repo.put("training_job", (school, course, job_id), row, TRAINING_TTL)

    def bundle(self, school, course):
        version = self.repo.active(school, course)
        key = (school, course, version)
        with self._bundle_lock:
            bundle = self._bundles.get(key)
            if bundle is None:
                _, bundle = self.repo.artifact(school, course, version)
                self._bundles[key] = bundle
                while len(self._bundles) > 4:
                    self._bundles.popitem(last=False)
            self._bundles.move_to_end(key)
        if bundle["graph_version"] != self.repo.graph(school, course)["version"]:
            raise ValueError("Knowledge prerequisites changed; retraining is required.")
        return version, bundle

    def predictions(self, actor, course):
        version, bundle = self.bundle(actor.school_id, course)
        student = actor.student_id
        snapshot = bundle["regression_snapshots"].get(student)
        profile = bundle["profiles"].get(student, {})
        grade, risk, missing = None, None, []
        if snapshot and snapshot.get("observed_grade_weeks", 0) + snapshot.get("observed_engagement_weeks", 0) > 0:
            frame = pd.DataFrame([snapshot])[bundle["regression_features"]].astype(float)
            grade = round(float(bundle["regression"].predict(frame)[0]), 4)
        else:
            missing.append("No recent complete-week observations for a personalized grade forecast.")
        features = profile.get("interaction_features", {})
        if features and all(name in features for name in bundle["classification_features"]):
            frame = pd.DataFrame([features])[bundle["classification_features"]]
            risk = int(bundle["classification"].predict(frame)[0])
        else:
            missing.append("No current interaction snapshot for a risk estimate.")
        evidence_times = [bundle["as_of"]]
        if risk is not None and profile.get("observed_at"):
            evidence_times.append(profile["observed_at"])
        if grade is not None and snapshot.get("feature_available_at"):
            evidence_times.append(snapshot["feature_available_at"])
        now = datetime.now(timezone.utc)
        stale = any((now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() > 7 * 86400
                    for value in evidence_times)
        return dict(grade=grade, risk=risk, source=bundle["source"], version=version, stale_data=stale,
            reference_at=bundle["as_of"], forecast_at=snapshot.get("timestamp") if snapshot else None,
            profile_observed_at=profile.get("observed_at"), missing_evidence=missing,
            limitations="Model estimates, not causal diagnoses or guaranteed outcomes.")

    def class_risk(self, actor, course, offset=0, limit=50):
        if actor.role not in {"admin", "instructor"}:
            raise PermissionError("Class risk requires course staff.")
        self.repo.member(actor, course)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Class risk pages require a nonnegative offset and limit 1..100.")
        version, bundle = self.bundle(actor.school_id, course)
        students = sorted(set(bundle["profiles"]) | set(bundle["regression_snapshots"]))
        rows = []
        for student in students[offset:offset + limit]:
            subject = Principal(actor.school_id, actor.principal_id, "student", student)
            prediction = self.predictions(subject, course)
            if prediction["version"] != version:
                raise QueueUnavailable("The active model changed while preparing the class page; retry.")
            rows.append(dict(student_id=student, **prediction))
        return dict(version=version, source=bundle["source"], reference_at=bundle["as_of"],
                    offset=offset, limit=limit, total=len(students), items=rows,
                    population="Students with evidence in the active course dataset; not a live enrollment roster.")

    def recommendations(self, actor, course, k=5, interests=None):
        version, bundle = self.bundle(actor.school_id, course)
        profile = bundle["profiles"].get(actor.student_id, {})
        rows = bundle["recommendation"].recommend(actor.student_id, k=k,
            mastery=profile.get("mastery"), weak_topics=profile.get("weak_topics"),
            interests=interests if interests is not None else profile.get("interests"))
        return dict(version=version, source=bundle["source"], reference_at=bundle["as_of"], items=rows)

    def advice(self, actor, course, request_id, k=5, interests=None):
        prediction = self.predictions(actor, course)
        recommendations = self.recommendations(actor, course, k, interests)
        version, bundle = self.bundle(actor.school_id, course)
        if prediction["version"] != recommendations["version"] or prediction["version"] != version:
            raise QueueUnavailable("The active model changed while preparing advice; retry this request.")
        profile = bundle["profiles"].get(actor.student_id, {})
        verbose = dict(prediction=prediction, recommendations=recommendations,
                       observed_topic_mastery=profile.get("mastery", {}))
        # Preserve exact recommendation order, run-length encode repeated topics,
        # and omit ranking diagnostics which the NLG writer must not reinterpret.
        groups = []
        for item in recommendations["items"]:
            if not groups or groups[-1][0] != item["topic_id"]:
                groups.append([item["topic_id"]])
            groups[-1].append(item["item_id"])
        compact = dict(g=prediction["grade"], r=prediction["risk"], src=bundle["source"],
            at=bundle["as_of"], stale=prediction["stale_data"], a=groups,
            w=profile.get("weak_topics", []))
        original_bytes, compact_bytes = len(dumps(verbose).encode()), len(dumps(compact).encode())
        job = self.queue.submit_advisor(
            Scope(actor.school_id, course, actor.principal_id, actor.student_id),
            compact, request_id,
            source_version=dict(model=prediction["version"], reference_at=bundle["as_of"], source=bundle["source"]))
        return dict(job=job, facts=prediction, recommendations=recommendations["items"],
            compression=dict(verbose_bytes=original_bytes, compact_bytes=compact_bytes,
                             byte_ratio=round(original_bytes / max(1, compact_bytes), 3),
                             measured="UTF-8 JSON bytes; not provider token counts"))


def worker_service():
    from redis import Redis
    from .runtime import QueueRuntime, RuntimeSettings
    settings = RuntimeSettings.from_env()
    queue = QueueRuntime(settings)
    return EngineService(Repository(queue.redis), queue)


@celery_app.task(bind=True, name="edudss_engine.train", ignore_result=True,
                 acks_late=True, soft_time_limit=850, time_limit=900)
def train_models(self, school, course, job_id):
    service = worker_service()
    lock = None
    acquired = False
    try:
        lock = service.repo.redis.lock(service.repo.key("train_lock", school, course), timeout=920)
        acquired = lock.acquire(blocking=False)
        if not acquired:
            raise self.retry(countdown=5, max_retries=120)
        service.run_training(school, course, job_id)
    finally:
        try:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    pass
        finally:
            service.queue.close()
