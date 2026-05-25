"""Giới hạn theo gói subscription."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from chat.models import Conversation, Message
from config import FREE_PLAN_DAILY_QUERIES, PRO_PLAN_DAILY_QUERIES


def daily_query_count(db: Session, user_id: int) -> int:
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        db.query(Message)
        .join(Conversation)
        .filter(
            Conversation.user_id == user_id,
            Message.role == "assistant",
            Message.created_at >= today_start,
        )
        .count()
    )


def plan_daily_limit(plan: str) -> int:
    if plan == "pro":
        return PRO_PLAN_DAILY_QUERIES
    return FREE_PLAN_DAILY_QUERIES


def enforce_query_quota(db: Session, user_id: int, plan: str) -> None:
    limit = plan_daily_limit(plan)
    used = daily_query_count(db, user_id)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Đã đạt giới hạn {limit} câu hỏi/ngày (gói {plan}). Nâng cấp Pro để tăng hạn mức.",
        )
