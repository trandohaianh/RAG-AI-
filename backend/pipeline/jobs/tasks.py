"""Các job của pipeline vận hành hằng ngày.

1. daily_kpis   — tổng hợp dữ liệu thô (tin nhắn, đánh giá, chi phí AI, ca hiện trường) → analytics_daily
2. data_quality — kiểm tra ràng buộc dữ liệu (kiểu Great Expectations, không cần cài GE)
3. retention    — xoá sự kiện chi phí thô quá hạn (đã được tổng hợp vào analytics_daily)
4. backup       — sao lưu SQLite (online backup API) + nén chỉ mục Chroma, giữ N bản gần nhất

Mỗi job nhận Session và trả (rows, details, status). Runner lo việc ghi pipeline_runs.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tarfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.models import User
from chat.models import Conversation, Message
from costs.models import LlmUsageEvent
from field.models import ServiceCase
from pipeline.jobs.models import AnalyticsDaily


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _p95(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))])


# --------------------------------------------------------------------------- 1. KPIs
def compute_day(db: Session, day: date) -> AnalyticsDaily:
    start, end = _bounds(day)
    in_day = lambda col: (col >= start, col < end)  # noqa: E731

    msgs = db.query(Message).filter(*in_day(Message.created_at))
    questions = msgs.filter(Message.role == "user").count()
    answers = msgs.filter(Message.role == "assistant").count()
    up = msgs.filter(Message.rating == 1).count()
    down = msgs.filter(Message.rating == -1).count()
    faith, relev = msgs.filter(Message.role == "assistant").with_entities(
        func.avg(Message.eval_faithfulness), func.avg(Message.eval_relevancy)).one()
    active = (
        db.query(func.count(func.distinct(Conversation.user_id)))
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Message.role == "user", *in_day(Message.created_at)).scalar() or 0
    )
    events = db.query(LlmUsageEvent).filter(*in_day(LlmUsageEvent.created_at))
    llm_requests, llm_cost, tokens_in, tokens_out = events.with_entities(
        func.count(LlmUsageEvent.id), func.coalesce(func.sum(LlmUsageEvent.cost_usd), 0.0),
        func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0),
        func.coalesce(func.sum(LlmUsageEvent.output_tokens + LlmUsageEvent.thinking_tokens), 0),
    ).one()
    llm_errors = events.filter(LlmUsageEvent.status != "ok").count()
    latencies = [row[0] for row in events.filter(
        LlmUsageEvent.latency_ms.isnot(None),
        LlmUsageEvent.feature.in_(("chat", "field_assistant", "public_api", "embed_chat")),
    ).with_entities(LlmUsageEvent.latency_ms).all()]

    row = db.get(AnalyticsDaily, day) or AnalyticsDaily(day=day)
    row.active_users = int(active)
    row.new_users = db.query(User).filter(*in_day(User.created_at)).count()
    row.questions, row.answers, row.thumbs_up, row.thumbs_down = questions, answers, up, down
    row.judge_faithfulness_avg = round(float(faith), 4) if faith is not None else None
    row.judge_relevancy_avg = round(float(relev), 4) if relev is not None else None
    row.llm_requests, row.llm_errors = int(llm_requests), int(llm_errors)
    row.tokens_in, row.tokens_out = int(tokens_in), int(tokens_out)
    row.llm_cost_usd = round(float(llm_cost), 6)
    row.llm_latency_p95_ms = _p95(latencies)
    row.cases_opened = db.query(ServiceCase).filter(*in_day(ServiceCase.created_at)).count()
    row.cases_resolved = db.query(ServiceCase).filter(
        ServiceCase.status == "resolved", *in_day(ServiceCase.updated_at)).count()
    row.computed_at = datetime.now(timezone.utc)
    db.merge(row)
    return row


def daily_kpis(db: Session, *, day: date | None = None, backfill_days: int = 2) -> tuple[int, dict, str]:
    """Tính lại `backfill_days` ngày gần nhất (mặc định hôm qua + hôm nay) để bắt dữ liệu đến trễ."""
    end_day = day or datetime.now(timezone.utc).date()
    days = [end_day - timedelta(days=i) for i in range(max(1, backfill_days))]
    summary = {}
    for d in days:
        row = compute_day(db, d)
        summary[d.isoformat()] = {"questions": row.questions, "llm_cost_usd": row.llm_cost_usd,
                                  "active_users": row.active_users}
    db.commit()
    return len(days), {"days": summary}, "ok"


# --------------------------------------------------------------------------- 2. data quality
def data_quality(db: Session, **_) -> tuple[int, dict, str]:
    """Mỗi check: số dòng vi phạm. Vi phạm nghiêm trọng → fail; còn lại → warn."""
    since = datetime.now(timezone.utc) - timedelta(days=2)
    checks: dict[str, dict] = {}

    def check(name: str, count: int, severity: str = "warn", hint: str = "") -> None:
        checks[name] = {"violations": int(count), "severity": severity, "hint": hint}

    check("usage_negative_values", db.query(LlmUsageEvent).filter(
        (LlmUsageEvent.input_tokens < 0) | (LlmUsageEvent.output_tokens < 0) | (LlmUsageEvent.cost_usd < 0)).count(),
        "fail", "Token/chi phí âm: lỗi ghi nhận usage")
    check("usage_unattributed_recent", db.query(LlmUsageEvent).filter(
        LlmUsageEvent.created_at >= since, LlmUsageEvent.user_id.is_(None),
        LlmUsageEvent.feature.notin_(("eval", "eval_ingest", "eval_judge"))).count(),
        "warn", "Gọi AI không gán được người dùng (thiếu usage_scope)")
    check("rating_out_of_range", db.query(Message).filter(
        Message.rating.isnot(None), Message.rating.notin_((-1, 0, 1))).count(), "fail")
    check("judge_score_out_of_range", db.query(Message).filter(
        ((Message.eval_faithfulness < 0) | (Message.eval_faithfulness > 1)) |
        ((Message.eval_relevancy < 0) | (Message.eval_relevancy > 1))).count(), "fail")
    check("empty_assistant_answers_recent", db.query(Message).filter(
        Message.role == "assistant", Message.created_at >= since,
        (Message.content.is_(None)) | (Message.content == "")).count(), "warn")
    answers_recent = db.query(Message).filter(Message.role == "assistant", Message.created_at >= since).count()
    chat_events_recent = db.query(LlmUsageEvent).filter(
        LlmUsageEvent.created_at >= since,
        LlmUsageEvent.feature.in_(("chat", "embed_chat", "public_api", "field_assistant"))).count()
    # Có câu trả lời mà không có sự kiện chi phí → cost tracking đang hỏng (hoặc trả lời "không có tài liệu").
    check("answers_without_cost_events", 1 if answers_recent >= 5 and chat_events_recent == 0 else 0, "warn",
          "Có câu trả lời nhưng không ghi nhận token nào trong 48h")
    check("cases_invalid_status", db.query(ServiceCase).filter(
        ServiceCase.status.notin_(("open", "in_progress", "waiting", "escalated", "resolved"))).count(), "fail")

    failed = [n for n, c in checks.items() if c["violations"] and c["severity"] == "fail"]
    warned = [n for n, c in checks.items() if c["violations"] and c["severity"] == "warn"]
    status = "fail" if failed else "warn" if warned else "ok"
    return len(checks), {"checks": checks, "failed": failed, "warned": warned}, status


# --------------------------------------------------------------------------- 3. retention
def retention(db: Session, *, keep_days: int | None = None, **_) -> tuple[int, dict, str]:
    keep = keep_days or int(os.getenv("USAGE_EVENT_RETENTION_DAYS", "400"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep)
    deleted = db.query(LlmUsageEvent).filter(LlmUsageEvent.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    from field.integrations import purge_old_records
    from field.photos import purge_expired_photos

    photos = purge_expired_photos(db)
    integration = purge_old_records(db)
    return int(deleted) + photos, {"cutoff": cutoff.isoformat(), "keep_days": keep, "usage_events_deleted": int(deleted),
                                   "field_photos_deleted": photos, **integration}, "ok"


# --------------------------------------------------------------------------- 4. backup
def backup(db: Session, *, keep: int | None = None, **_) -> tuple[int, dict, str]:
    from config import CHROMA_DIR, DATA_ROOT, DATABASE_URL

    keep = keep or int(os.getenv("BACKUP_KEEP", "7"))
    target = Path(os.getenv("BACKUP_DIR", str(DATA_ROOT / "backups")))
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    made = []
    if DATABASE_URL.startswith("sqlite:///"):
        source = DATABASE_URL.removeprefix("sqlite:///")
        dest = target / f"app-{stamp}.db"
        # Online backup: nhất quán kể cả khi app đang ghi.
        with sqlite3.connect(source) as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
        made.append(dest.name)
    if CHROMA_DIR.exists():
        archive = target / f"chroma-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(CHROMA_DIR, arcname="chroma_db")
        made.append(archive.name)
    from config import FIELD_PHOTO_DIR

    if FIELD_PHOTO_DIR.exists() and any(FIELD_PHOTO_DIR.iterdir()):
        archive = target / f"photos-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(FIELD_PHOTO_DIR, arcname="field_photos")
        made.append(archive.name)
    for prefix in ("app-", "chroma-", "photos-"):
        olds = sorted(p for p in target.iterdir() if p.name.startswith(prefix))
        for stale in olds[:-keep]:
            stale.unlink(missing_ok=True) if stale.is_file() else shutil.rmtree(stale, ignore_errors=True)
    size = sum(p.stat().st_size for p in target.iterdir() if p.is_file())
    status = "ok" if made else "warn"
    note = None if DATABASE_URL.startswith("sqlite") else "DB không phải SQLite: dùng backup của nhà cung cấp (Neon PITR / pg_dump)"
    return len(made), {"files": made, "dir": str(target), "total_bytes": size, "note": note}, status


JOBS = {
    "daily_kpis": daily_kpis,
    "data_quality": data_quality,
    "retention": retention,
    "backup": backup,
}
