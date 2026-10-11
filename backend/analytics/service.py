"""Thống kê usage từ SQLite."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from chat.models import Conversation, Message
from config import DATABASE_URL
from saas.limits import daily_query_count, plan_daily_limit
from rag.ingest import list_user_docs


def user_overview(db: Session, user_id: int, plan: str) -> dict:
    docs = None  # async below — caller passes doc_count
    conv_count = db.query(Conversation).filter(Conversation.user_id == user_id).count()
    msg_count = (
        db.query(Message)
        .join(Conversation)
        .filter(Conversation.user_id == user_id)
        .count()
    )
    assistant_count = (
        db.query(Message)
        .join(Conversation)
        .filter(Conversation.user_id == user_id, Message.role == "assistant")
        .count()
    )
    feedback_up = (
        db.query(Message)
        .join(Conversation)
        .filter(Conversation.user_id == user_id, Message.rating == 1)
        .count()
    )
    feedback_down = (
        db.query(Message)
        .join(Conversation)
        .filter(Conversation.user_id == user_id, Message.rating == -1)
        .count()
    )
    queries_today = daily_query_count(db, user_id)
    avg_faithfulness, avg_relevancy = (
        db.query(func.avg(Message.eval_faithfulness), func.avg(Message.eval_relevancy))
        .join(Conversation)
        .filter(Conversation.user_id == user_id, Message.eval_faithfulness.isnot(None))
        .one()
    )
    return {
        "conversations": conv_count,
        "messages": msg_count,
        "assistant_replies": assistant_count,
        "feedback_up": feedback_up,
        "feedback_down": feedback_down,
        "avg_faithfulness": round(avg_faithfulness, 3) if avg_faithfulness is not None else None,
        "avg_relevancy": round(avg_relevancy, 3) if avg_relevancy is not None else None,
        "queries_today": queries_today,
        "daily_limit": plan_daily_limit(plan),
        "subscription_plan": plan,
    }


async def user_overview_with_docs(db: Session, user_id: int, plan: str) -> dict:
    docs = await list_user_docs(user_id)
    base = user_overview(db, user_id, plan)
    base["documents"] = len(docs)
    base["total_chunks"] = sum(d["chunks"] for d in docs)
    return base


def daily_series(db: Session, user_id: int, days: int = 14) -> list[dict]:
    """Số câu trả lời assistant theo ngày (UTC)."""
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days - 1)
    if DATABASE_URL.startswith("sqlite"):
        day_col = func.strftime("%Y-%m-%d", Message.created_at)
    else:
        day_col = func.date(Message.created_at)
    rows = (
        db.query(
            day_col.label("day"),
            func.count(Message.id).label("cnt"),
        )
        .join(Conversation)
        .filter(
            Conversation.user_id == user_id,
            Message.role == "assistant",
            Message.created_at >= start,
        )
        .group_by(day_col)
        .all()
    )
    by_day = {str(r.day)[:10]: int(r.cnt) for r in rows}
    out: list[dict] = []
    for i in range(days):
        d = start + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        out.append({"date": key, "queries": by_day.get(key, 0)})
    return out
