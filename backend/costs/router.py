"""API chi phí: tổng hợp cho admin, chi phí hạ tầng, và mức dùng của chính người dùng."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from costs.budget import daily_limits, spent_today
from costs.models import InfraCost, LlmUsageEvent
from costs.pricing import PRICING_CHECKED_AT, billing_tier, price_table
from database import get_db
from dependencies import get_current_user, require_admin

router = APIRouter(tags=["costs"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class InfraCostIn(BaseModel):
    month: str = Field(..., description="YYYY-MM")
    provider: str = Field(..., min_length=1, max_length=60)
    item: str = Field(..., min_length=1, max_length=120)
    amount_usd: float = Field(..., ge=0, le=100000)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("month")
    @classmethod
    def valid_month(cls, value: str) -> str:
        if not _MONTH_RE.match(value):
            raise ValueError("month phải có dạng YYYY-MM")
        return value


class InfraCostOut(InfraCostIn):
    id: int
    model_config = {"from_attributes": True}


def _window(days: int) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    return end - timedelta(days=days), end


def _month_bounds(today: date) -> tuple[datetime, datetime, int]:
    start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    return start, start + timedelta(days=days_in_month), days_in_month


def _aggregate(db: Session, start: datetime, end: datetime, user_id: int | None = None):
    base = db.query(LlmUsageEvent).filter(
        LlmUsageEvent.created_at >= start, LlmUsageEvent.created_at < end
    )
    if user_id is not None:
        base = base.filter(LlmUsageEvent.user_id == user_id)
    return base


def _sum_cols():
    return (
        func.coalesce(func.sum(LlmUsageEvent.cost_usd), 0.0),
        func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0),
        func.coalesce(func.sum(LlmUsageEvent.output_tokens + LlmUsageEvent.thinking_tokens), 0),
        func.count(LlmUsageEvent.id),
    )


def _rows_to_breakdown(rows, key: str) -> list[dict]:
    return [
        {key: name, "cost_usd": round(float(cost), 6), "input_tokens": int(inp),
         "output_tokens": int(out), "requests": int(count)}
        for name, cost, inp, out, count in rows
    ]


def build_summary(db: Session, days: int, user_id: int | None = None) -> dict:
    start, end = _window(days)
    scoped = _aggregate(db, start, end, user_id)
    cost, inp, out, count = scoped.with_entities(*_sum_cols()).one()

    from database import utc_date

    day_col = utc_date(LlmUsageEvent.created_at, db)
    by_day_rows = (
        scoped.with_entities(day_col, *_sum_cols()).group_by(day_col).order_by(day_col).all()
    )
    by_day_map = {str(r[0]): r for r in by_day_rows}
    by_day = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        row = by_day_map.get(day)
        by_day.append({
            "date": day,
            "cost_usd": round(float(row[1]), 6) if row else 0.0,
            "input_tokens": int(row[2]) if row else 0,
            "output_tokens": int(row[3]) if row else 0,
            "requests": int(row[4]) if row else 0,
        })

    by_feature = _rows_to_breakdown(
        scoped.with_entities(LlmUsageEvent.feature, *_sum_cols())
        .group_by(LlmUsageEvent.feature).order_by(func.sum(LlmUsageEvent.cost_usd).desc()).all(),
        "feature",
    )
    by_model = _rows_to_breakdown(
        scoped.with_entities(LlmUsageEvent.model, *_sum_cols())
        .group_by(LlmUsageEvent.model).order_by(func.sum(LlmUsageEvent.cost_usd).desc()).all(),
        "model",
    )
    chat_queries = (
        scoped.filter(LlmUsageEvent.feature.in_(("chat", "public_api", "embed_chat", "field_assistant")))
        .with_entities(func.count(LlmUsageEvent.id)).scalar() or 0
    )
    estimated = scoped.filter(LlmUsageEvent.estimated.is_(True)).with_entities(
        func.count(LlmUsageEvent.id)).scalar() or 0
    errors = scoped.filter(LlmUsageEvent.status != "ok").with_entities(
        func.count(LlmUsageEvent.id)).scalar() or 0

    summary = {
        "period": {"days": days, "start": start.isoformat(), "end": end.isoformat()},
        "billing_tier": billing_tier(),
        "pricing_checked_at": PRICING_CHECKED_AT,
        "totals": {
            "llm_usd": round(float(cost), 6),
            "input_tokens": int(inp),
            "output_tokens": int(out),
            "requests": int(count),
            "answer_requests": int(chat_queries),
            "cost_per_answer_usd": round(float(cost) / chat_queries, 6) if chat_queries else 0.0,
            "estimated_events": int(estimated),
            "error_events": int(errors),
        },
        "by_day": by_day,
        "by_feature": by_feature,
        "by_model": by_model,
    }
    return summary


@router.get("/admin/costs/summary")
def admin_cost_summary(
    days: int = Query(30, ge=1, le=366),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    summary = build_summary(db, days)

    start, end = _window(days)
    top = (
        _aggregate(db, start, end)
        .filter(LlmUsageEvent.user_id.isnot(None))
        .with_entities(LlmUsageEvent.user_id, *_sum_cols())
        .group_by(LlmUsageEvent.user_id)
        .order_by(func.sum(LlmUsageEvent.cost_usd).desc())
        .limit(10).all()
    )
    emails = dict(
        db.query(User.id, User.email).filter(User.id.in_([r[0] for r in top])).all()
    ) if top else {}
    summary["top_users"] = [
        {"user_id": uid, "email": emails.get(uid, f"#{uid}"), "cost_usd": round(float(c), 6),
         "input_tokens": int(i), "output_tokens": int(o), "requests": int(n)}
        for uid, c, i, o, n in top
    ]

    today = datetime.now(timezone.utc).date()
    month_start, month_end, days_in_month = _month_bounds(today)
    month_key = today.strftime("%Y-%m")
    infra_rows = db.query(InfraCost).filter(InfraCost.month == month_key).all()
    infra_month = round(sum(r.amount_usd for r in infra_rows), 4)
    llm_mtd = float(_aggregate(db, month_start, month_end).with_entities(
        func.coalesce(func.sum(LlmUsageEvent.cost_usd), 0.0)).scalar() or 0.0)
    elapsed = max(1, today.day)
    llm_projected = llm_mtd / elapsed * days_in_month

    global_limit, user_limit = daily_limits()
    summary["month"] = {
        "month": month_key,
        "llm_to_date_usd": round(llm_mtd, 6),
        "llm_projected_usd": round(llm_projected, 6),
        "infra_usd": infra_month,
        "total_projected_usd": round(llm_projected + infra_month, 6),
        "infra_items": [InfraCostOut.model_validate(r).model_dump() for r in infra_rows],
    }
    summary["budget"] = {
        "daily_limit_usd": global_limit,
        "user_daily_limit_usd": user_limit,
        "spent_today_usd": round(spent_today(db), 6),
    }
    summary["prices"] = price_table()
    return summary


@router.get("/admin/costs/events")
def admin_cost_events(
    limit: int = Query(50, ge=1, le=500),
    feature: str | None = Query(None, max_length=40),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(LlmUsageEvent).order_by(LlmUsageEvent.id.desc())
    if feature:
        query = query.filter(LlmUsageEvent.feature == feature)
    return [
        {
            "id": e.id, "created_at": e.created_at.isoformat() if e.created_at else None,
            "user_id": e.user_id, "workspace_id": e.workspace_id, "feature": e.feature,
            "provider": e.provider, "model": e.model, "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens, "thinking_tokens": e.thinking_tokens,
            "units": e.units, "estimated": e.estimated, "cost_usd": e.cost_usd,
            "latency_ms": e.latency_ms, "status": e.status,
        }
        for e in query.limit(limit).all()
    ]


@router.get("/admin/costs/infra", response_model=list[InfraCostOut])
def list_infra_costs(
    month: str | None = Query(None),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(InfraCost).order_by(InfraCost.month.desc(), InfraCost.id)
    if month:
        if not _MONTH_RE.match(month):
            raise HTTPException(status_code=422, detail="month phải có dạng YYYY-MM")
        query = query.filter(InfraCost.month == month)
    return query.all()


@router.post("/admin/costs/infra", response_model=InfraCostOut)
def add_infra_cost(
    body: InfraCostIn,
    admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = InfraCost(**body.model_dump(), created_by=admin.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    _refresh_infra_gauge(db)
    return row


@router.delete("/admin/costs/infra/{cost_id}")
def delete_infra_cost(
    cost_id: int,
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.get(InfraCost, cost_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy khoản chi")
    db.delete(row)
    db.commit()
    _refresh_infra_gauge(db)
    return {"message": "Đã xoá"}


@router.get("/costs/me")
def my_costs(
    days: int = Query(30, ge=1, le=366),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    summary = build_summary(db, days, user_id=user.id)
    _, user_limit = daily_limits()
    summary["budget"] = {
        "user_daily_limit_usd": user_limit,
        "spent_today_usd": round(spent_today(db, user.id), 6),
    }
    return summary


def _refresh_infra_gauge(db: Session) -> None:
    try:
        from metrics import INFRA_COST_MONTH_USD

        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        total = db.query(func.coalesce(func.sum(InfraCost.amount_usd), 0.0)).filter(
            InfraCost.month == month_key).scalar() or 0.0
        INFRA_COST_MONTH_USD.set(float(total))
    except Exception:
        pass


def refresh_infra_gauge() -> None:
    from database import SessionLocal

    with SessionLocal() as db:
        _refresh_infra_gauge(db)
