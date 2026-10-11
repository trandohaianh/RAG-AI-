"""Analytics dashboard API — tuần 5–6."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from analytics import service as analytics_service
from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


class OverviewOut(BaseModel):
    conversations: int
    messages: int
    assistant_replies: int
    feedback_up: int
    feedback_down: int
    avg_faithfulness: float | None = None
    avg_relevancy: float | None = None
    queries_today: int
    daily_limit: int
    subscription_plan: str
    documents: int
    total_chunks: int


class DailyPoint(BaseModel):
    date: str
    queries: int


@router.get("/overview", response_model=OverviewOut)
async def overview(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await analytics_service.user_overview_with_docs(
        db, user.id, user.subscription_plan
    )
    return OverviewOut(**data)


@router.get("/daily", response_model=list[DailyPoint])
def daily(
    days: int = 14,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    days = min(max(days, 7), 90)
    return analytics_service.daily_series(db, user.id, days)
