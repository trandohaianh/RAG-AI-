"""Chạy job pipeline và ghi nhật ký vào pipeline_runs.

  python -m pipeline.jobs.runner                 # chạy đủ chuỗi daily
  python -m pipeline.jobs.runner --only backup   # một job
  python -m pipeline.jobs.runner --backfill 30   # tính lại KPI 30 ngày
  python -m pipeline.jobs.runner --schedule      # tiến trình nền, chạy mỗi ngày lúc PIPELINE_DAILY_AT (UTC)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger("fieldops.pipeline")
DAILY_CHAIN = ["daily_kpis", "data_quality", "retention", "backup"]


def run_job(name: str, *, trigger: str = "manual", session_factory=None, **kwargs) -> dict:
    from database import SessionLocal
    from pipeline.jobs.models import PipelineRun
    from pipeline.jobs.tasks import JOBS

    factory = session_factory or SessionLocal
    try:
        with factory() as db:
            run = PipelineRun(job=name, status="running", trigger=trigger, started_at=datetime.now(timezone.utc))
            db.add(run)
            db.commit()
            run_id = run.id
    except Exception as exc:
        # DB tạm mất kết nối: báo lỗi cho lượt này thay vì làm chết cả scheduler.
        logger.exception("Pipeline job %s could not be recorded", name)
        return {"job": name, "status": "fail", "rows": 0,
                "details": {"error": type(exc).__name__, "message": "could not record run"}}
    started = time.perf_counter()
    try:
        with factory() as db:
            rows, details, status = JOBS[name](db, **kwargs)
    except Exception as exc:
        rows, status = 0, "fail"
        details = {"error": type(exc).__name__, "message": str(exc)[:500],
                   "trace": traceback.format_exc(limit=5)[-2000:]}
        logger.exception("Pipeline job %s failed", name)
    details["duration_s"] = round(time.perf_counter() - started, 3)
    with factory() as db:
        run = db.get(PipelineRun, run_id)
        run.status, run.rows = status, rows
        run.finished_at = datetime.now(timezone.utc)
        run.details_json = json.dumps(details, ensure_ascii=False, default=str)
        db.commit()
    logger.info("pipeline %s → %s (%s rows, %.2fs)", name, status, rows, details["duration_s"])
    return {"job": name, "status": status, "rows": rows, "details": details}


@contextmanager
def _chain_lock():
    """Khoá file dùng chung qua volume data: container pipeline (lịch) và API (nút chạy tay)
    là hai tiến trình khác nhau; khoá threading trong API không ngăn được hai chuỗi chạy
    song song (daily_kpis chèn trùng AnalyticsDaily -> IntegrityError, backup chồng nhau)."""
    from config import CHROMA_DIR
    from rag.document_lifecycle import storage_lock

    with storage_lock(CHROMA_DIR, "__pipeline_chain__"):
        yield


def run_chain(jobs: list[str] | None = None, *, trigger: str = "schedule", backfill: int = 2,
              session_factory=None) -> list[dict]:
    results = []
    with _chain_lock():
        for name in jobs or DAILY_CHAIN:
            kwargs = {"backfill_days": backfill} if name == "daily_kpis" else {}
            results.append(run_job(name, trigger=trigger, session_factory=session_factory, **kwargs))
    return results


def fail_interrupted_runs(older_than: timedelta = timedelta(hours=6), session_factory=None) -> int:
    """Đánh dấu `fail` các lượt còn `running` từ trước (tiến trình bị kill giữa chừng, ví dụ
    `docker compose up -d --build` khởi động lại container lúc đang backup). Nếu không, dashboard
    mãi hiển thị chúng là lượt mới nhất đang chạy."""
    from database import SessionLocal
    from pipeline.jobs.models import PipelineRun

    cutoff = datetime.now(timezone.utc) - older_than
    with (session_factory or SessionLocal)() as db:
        rows = db.query(PipelineRun).filter(PipelineRun.status == "running",
                                            PipelineRun.started_at < cutoff).all()
        for row in rows:
            row.status, row.finished_at = "fail", datetime.now(timezone.utc)
            row.details_json = json.dumps({"error": "interrupted", "message": "process stopped before finishing"})
        db.commit()
        return len(rows)


def _next_run(now: datetime, at: str) -> datetime:
    hour, minute = (int(x) for x in at.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _last_success(job: str) -> datetime | None:
    from database import SessionLocal
    from pipeline.jobs.models import PipelineRun

    with SessionLocal() as db:
        row = (db.query(PipelineRun).filter(PipelineRun.job == job, PipelineRun.status.in_(("ok", "warn")))
               .order_by(PipelineRun.id.desc()).first())
        if row and row.finished_at:
            return row.finished_at if row.finished_at.tzinfo else row.finished_at.replace(tzinfo=timezone.utc)
    return None


def schedule_forever() -> None:
    """Scheduler tối giản cho VPS free (thay Airflow ~1.5GB RAM). Chạy bù nếu lần cuối > 26 giờ."""
    from database import init_db

    init_db()
    at = os.getenv("PIPELINE_DAILY_AT", "02:00")
    quality_every = int(os.getenv("PIPELINE_QUALITY_EVERY_MIN", "60")) * 60
    try:
        stale = fail_interrupted_runs()
        if stale:
            logger.warning("Marked %s interrupted pipeline run(s) as fail", stale)
    except Exception:
        logger.exception("could not clean up interrupted runs")
    # Chuỗi hằng ngày kết thúc bằng backup, chỉ chạy trong chuỗi đầy đủ. daily_kpis thì chạy
    # mỗi giờ nên lấy nó làm mốc thì chạy bù không bao giờ xảy ra sau khi lỡ lịch 02:00.
    last = _last_success("backup")
    if last is None or datetime.now(timezone.utc) - last > timedelta(hours=26):
        logger.info("Catch-up run (last success: %s)", last)
        _safe_chain(trigger="catch_up")
    next_daily = _next_run(datetime.now(timezone.utc), at)
    next_quality = time.monotonic() + quality_every
    while True:
        now = datetime.now(timezone.utc)
        if now >= next_daily:
            _safe_chain(trigger="schedule")
            next_daily = _next_run(datetime.now(timezone.utc), at)
        try:   # webhook ra CRM/NMS: gửi lại các lượt đến hạn (backoff), quá số lần -> dead-letter
            from database import SessionLocal
            from field.integrations import dispatch_due

            with SessionLocal() as db:
                dispatch_due(db)
        except Exception:
            logger.exception("webhook dispatch failed")
        if time.monotonic() >= next_quality:
            # KPI hôm nay + kiểm tra chất lượng mỗi giờ để dashboard gần thời gian thực.
            _safe_chain(["daily_kpis", "data_quality"], trigger="hourly", backfill=1)
            next_quality = time.monotonic() + quality_every
        time.sleep(30)


def _safe_chain(jobs: list[str] | None = None, **kwargs) -> None:
    try:
        run_chain(jobs, **kwargs)
    except Exception:   # vòng lặp lịch phải sống qua lỗi tạm thời (DB, khoá, đĩa)
        logger.exception("pipeline chain failed")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", choices=DAILY_CHAIN)
    parser.add_argument("--backfill", type=int, default=2)
    parser.add_argument("--schedule", action="store_true")
    args = parser.parse_args(argv)
    if args.schedule:
        schedule_forever()
        return 0
    from database import init_db

    init_db()
    results = run_chain(args.only, trigger="manual", backfill=args.backfill)
    for r in results:
        print(f"{r['job']:<13} {r['status']:<5} rows={r['rows']}")
    return 1 if any(r["status"] == "fail" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
