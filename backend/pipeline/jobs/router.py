"""API admin cho pipeline vận hành: lịch sử chạy, KPI theo ngày, chạy tay."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth.schemas import CurrentUser
from database import get_db
from dependencies import require_admin
from pipeline.jobs.models import AnalyticsDaily, PipelineRun

router = APIRouter(prefix="/admin/pipeline", tags=["pipeline"])
_manual_lock = threading.Lock()


def _run_out(r: PipelineRun) -> dict:
    return {
        "id": r.id,
        "job": r.job,
        "status": r.status,
        "rows": r.rows,
        "trigger": r.trigger,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "details": json.loads(r.details_json) if r.details_json else None,
    }


@router.get("/runs")
def list_runs(
    limit: int = Query(30, ge=1, le=200),
    job: str | None = None,
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(PipelineRun).order_by(PipelineRun.id.desc())
    if job:
        query = query.filter(PipelineRun.job == job)
    return [_run_out(r) for r in query.limit(limit).all()]


@router.get("/status")
def pipeline_status(_admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    from pipeline.jobs.runner import DAILY_CHAIN

    out = {}
    for job in DAILY_CHAIN:
        last = db.query(PipelineRun).filter(PipelineRun.job == job).order_by(PipelineRun.id.desc()).first()
        out[job] = _run_out(last) if last else None
    return out


@router.get("/daily")
def daily_kpis(
    days: int = Query(30, ge=1, le=366), _admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)
):
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = db.query(AnalyticsDaily).filter(AnalyticsDaily.day >= since).order_by(AnalyticsDaily.day).all()
    return [
        {
            c.name: (getattr(r, c.name).isoformat() if hasattr(getattr(r, c.name), "isoformat") else getattr(r, c.name))
            for c in AnalyticsDaily.__table__.columns
        }
        for r in rows
    ]


@router.post("/run")
def trigger_run(job: str | None = Query(None), _admin: CurrentUser = Depends(require_admin)):
    from pipeline.jobs.runner import DAILY_CHAIN, run_chain

    if job and job not in DAILY_CHAIN:
        raise HTTPException(status_code=422, detail=f"job phải thuộc {DAILY_CHAIN}")
    if not _manual_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Pipeline đang chạy")

    def work():
        try:
            run_chain([job] if job else None, trigger="manual")
        finally:
            _manual_lock.release()

    threading.Thread(target=work, daemon=True, name="pipeline-manual").start()
    return {"message": "Đã bắt đầu chạy pipeline", "jobs": [job] if job else DAILY_CHAIN}
