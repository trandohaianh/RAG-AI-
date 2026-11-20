"""API credentials and isolated public-widget guest sessions."""

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Default")
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmbedToken(Base):
    __tablename__ = "embed_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(120), default="Website")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    allowed_origins: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmbedGuestSession(Base):
    __tablename__ = "embed_guest_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    embed_token_id: Mapped[int] = mapped_column(ForeignKey("embed_tokens.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyQueryUsage(Base):
    """Accepted RAG attempts; deliberately independent of conversation lifetime."""

    __tablename__ = "daily_query_usage"
    __table_args__ = (CheckConstraint("used_queries >= 0", name="ck_query_usage_nonnegative"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    used_queries: Mapped[int] = mapped_column(default=0, server_default="0")


class QueryUsageMigration(Base):
    """Makes the legacy usage snapshot run once across workers and restarts."""

    __tablename__ = "query_usage_migrations"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
