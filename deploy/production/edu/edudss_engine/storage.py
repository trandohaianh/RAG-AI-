"""Course-scoped Redis records and authenticated, server-created model artifacts."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import uuid
import joblib
import networkx as nx

from .auth import Principal, signing_key
from .runtime import Scope


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    def normalize(item):
        if hasattr(item, "item"):
            return item.item()
        if hasattr(item, "isoformat"):
            return item.isoformat()
        raise TypeError("Unsupported JSON value.")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=normalize)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


class Repository:
    def __init__(self, client, key=None):
        self.redis = client
        self.signing_key = signing_key(key)

    def key(self, kind, *parts):
        return "edudss:catalog:v1:" + kind + ":" + digest(list(parts))

    def get(self, kind, *parts):
        raw = self.redis.get(self.key(kind, *parts))
        return json.loads(raw) if raw is not None else None

    def put(self, kind, parts, value, ttl=None):
        self.redis.set(self.key(kind, *parts), dumps(value), ex=ttl)
        return value

    def member(self, actor: Principal, course_id):
        if not course_id.strip() or len(course_id) > 128:
            raise ValueError("Explicit course identity is required.")
        if actor.role == "admin":
            return
        row = self.get("member", actor.school_id, course_id, actor.principal_id)
        if not row or not row["active"] or row["role"] != actor.role or (
            actor.role == "student" and row.get("student_id") != actor.student_id):
            raise PermissionError("Current course membership is required.")

    def set_member(self, actor, course_id, principal_id, role, student_id=None, active=True):
        if actor.role != "admin":
            raise PermissionError("School administrator required.")
        row = dict(principal_id=principal_id, role=role, student_id=student_id, active=active)
        return self.put("member", (actor.school_id, course_id, principal_id), row)

    def graph(self, school, course):
        return self.get("graph", school, course) or {"edges": [], "version": digest([])}

    def set_graph(self, school, course, edges):
        graph = nx.DiGraph()
        graph.add_edges_from(edges)
        if any(not isinstance(node, str) or not node.strip() or len(node) > 128 for node in graph):
            raise ValueError("Knowledge nodes need explicit bounded names.")
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("Knowledge prerequisites must be acyclic.")
        ordered = sorted([list(edge) for edge in graph.edges()])
        return self.put("graph", (school, course), dict(edges=ordered, version=digest(ordered)))

    def put_dataset(self, school, course, payload):
        for field in ("events", "classification", "items", "ratings"):
            for row in payload[field]:
                if str(row.get("school_id", school)) != school or str(row.get("course_id", course)) != course:
                    raise ValueError("Dataset rows must belong to the authorized school/course.")
                row["school_id"], row["course_id"] = school, course
        record = dict(id=uuid.uuid4().hex, school_id=school, course_id=course,
                      created_at=utcnow(), payload=payload)
        self.put("dataset", (school, course, record["id"]), record)
        return {key: value for key, value in record.items() if key != "payload"}

    def dataset(self, school, course, dataset_id):
        row = self.get("dataset", school, course, dataset_id)
        if not row:
            raise LookupError("Dataset unavailable.")
        return row

    def save_artifact(self, school, course, bundle, report):
        buffer = io.BytesIO()
        joblib.dump(bundle, buffer, compress=3)
        raw = buffer.getvalue()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Model bundle exceeds the configured artifact bound.")
        version = uuid.uuid4().hex
        signed = dumps([school, course, version]).encode() + b"\0" + raw
        tag = hmac.new(self.signing_key.encode(), signed, hashlib.sha256).hexdigest()
        record = dict(version=version, tag=tag, blob=base64.b64encode(raw).decode(),
                      report=report, created_at=utcnow())
        self.put("artifact", (school, course, version), record)
        return version

    def active(self, school, course):
        version = self.redis.get(self.key("active", school, course))
        if isinstance(version, bytes):
            version = version.decode()
        if not version:
            raise LookupError("No trained model is active for this course.")
        return version

    def report(self, school, course, version=None):
        version = version or self.active(school, course)
        row = self.get("artifact", school, course, version)
        if not row:
            raise LookupError("Model unavailable.")
        return dict(version=version, created_at=row["created_at"], **row["report"])

    def artifact(self, school, course, version=None):
        version = version or self.active(school, course)
        row = self.get("artifact", school, course, version)
        if not row:
            raise LookupError("Model unavailable.")
        raw = base64.b64decode(row["blob"], validate=True)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Model artifact is invalid.")
        signed = dumps([school, course, version]).encode() + b"\0" + raw
        tag = hmac.new(self.signing_key.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag, row["tag"]):
            raise ValueError("Model artifact integrity verification failed.")
        # Only an authenticated artifact generated by this server can reach joblib.
        return version, joblib.load(io.BytesIO(raw))

    def activate(self, school, course, version, requested_job=None):
        if requested_job:
            script = """
            if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
            redis.call('SET', KEYS[2], ARGV[2]); return 1
            """
            return bool(self.redis.eval(script, 2, self.key("training_request", school, course),
                self.key("active", school, course), requested_job, version))
        self.redis.set(self.key("active", school, course), version)
        return True

    def training_job(self, actor, course, job_id):
        row = self.get("training_job", actor.school_id, course, job_id)
        if row is None or row["owner"] != actor.principal_id:
            return None
        return {key: value for key, value in row.items() if key not in {"owner", "dataset_id", "role"}}

    def put_lecture(self, school, course, lecture_id, title, paragraphs):
        version = digest(dict(title=title, paragraphs=paragraphs))
        previous = self.get("lecture", school, course, lecture_id)
        if previous and previous["version"] == version:
            return previous
        record = dict(lecture_id=lecture_id, title=title, version=version,
            approved=False, paragraphs=paragraphs, created_at=utcnow())
        return self.put("lecture", (school, course, lecture_id), record)


    def approve_lecture(self, school, course, lecture_id, version, approved):
        script = """
        local raw = redis.call('GET',KEYS[1])
        if not raw then return 'MISSING' end
        local value = cjson.decode(raw)
        if value.version ~= ARGV[1] then return 'CONFLICT' end
        value.approved = ARGV[2] == 'true'
        local updated = cjson.encode(value)
        redis.call('SET',KEYS[1],updated)
        return updated
        """
        raw = self.redis.eval(script, 1, self.key("lecture", school, course, lecture_id),
                              version, "true" if approved else "false")
        if isinstance(raw, bytes):
            raw = raw.decode()
        return raw if raw in {"MISSING", "CONFLICT"} else json.loads(raw)
