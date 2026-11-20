"""Bảng của pipeline vận hành: nhật ký chạy job và KPI tổng hợp theo ngày (lớp gold của app DB)."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|ok|warn|fail
    rows: Mapped[int] = mapped_column(Integer, default=0)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), default="schedule")


class AnalyticsDaily(Base):
    """KPI một ngày (UTC). Ghi đè được (idempotent) khi chạy lại/backfill."""

    __tablename__ = "analytics_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    active_users: Mapped[int] = mapped_column(Integer, default=0)
    new_users: Mapped[int] = mapped_column(Integer, default=0)
    questions: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[int] = mapped_column(Integer, default=0)
    thumbs_up: Mapped[int] = mapped_column(Integer, default=0)
    thumbs_down: Mapped[int] = mapped_column(Integer, default=0)
    judge_faithfulness_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_relevancy_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_requests: Mapped[int] = mapped_column(Integer, default=0)
    llm_errors: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    llm_latency_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cases_opened: Mapped[int] = mapped_column(Integer, default=0)
    cases_resolved: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
