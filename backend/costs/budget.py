"""Chặn gọi AI khi vượt ngân sách ngày (toàn hệ thống hoặc từng người dùng).

LLM_DAILY_BUDGET_USD=0 / LLM_USER_DAILY_BUDGET_USD=0 nghĩa là không giới hạn.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from costs.models import LlmUsageEvent


def _limit(name: str) -> float:
    try:
        value = float(os.getenv(name, "0") or 0)
    except ValueError:
        return 0.0
    return value if value > 0 else 0.0


def daily_limits() -> tuple[float, float]:
    return _limit("LLM_DAILY_BUDGET_USD"), _limit("LLM_USER_DAILY_BUDGET_USD")


def _day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def spent_today(db: Session, user_id: int | None = None) -> float:
    start = _day_start()
    query = db.query(func.coalesce(func.sum(LlmUsageEvent.cost_usd), 0.0)).filter(
        LlmUsageEvent.created_at >= start,
        LlmUsageEvent.created_at < start + timedelta(days=1),
    )
    if user_id is not None:
        query = query.filter(LlmUsageEvent.user_id == user_id)
    return float(query.scalar() or 0.0)


def enforce_budget(db: Session, user_id: int | None) -> None:
    """Raise 429 nếu đã chạm ngân sách ngày."""
    global_limit, user_limit = daily_limits()
    if not global_limit and not user_limit:
        return
    if global_limit and spent_today(db) >= global_limit:
        raise HTTPException(
            status_code=429,
            detail="Hệ thống đã dùng hết ngân sách AI hôm nay. Vui lòng thử lại vào ngày mai.",
            headers={"Retry-After": "3600"},
        )
    if user_limit and user_id is not None and spent_today(db, user_id) >= user_limit:
        raise HTTPException(
            status_code=429,
            detail="Bạn đã dùng hết hạn mức AI hôm nay.",
            headers={"Retry-After": "3600"},
        )
