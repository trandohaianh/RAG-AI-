"""Bảng lưu sự kiện dùng AI và chi phí hạ tầng theo tháng."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LlmUsageEvent(Base):
    """Một lần gọi model (sinh văn bản, embedding, OCR, rerank)."""

    __tablename__ = "llm_usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Không đặt FK: sự kiện chi phí phải giữ lại kể cả khi tài khoản bị xoá.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[float] = mapped_column(Float, default=0.0)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ok")


class InfraCost(Base):
    """Chi phí hạ tầng nhập tay theo tháng (VPS, domain, dịch vụ ngoài)."""

    __tablename__ = "infra_costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    provider: Mapped[str] = mapped_column(String(60))
    item: Mapped[str] = mapped_column(String(120))
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
