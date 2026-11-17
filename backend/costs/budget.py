"""Chặn gọi AI khi vượt ngân sách ngày (toàn hệ thống hoặc từng người dùng).

LLM_DAILY_BUDGET_USD=0 / LLM_USER_DAILY_BUDGET_USD=0 nghĩa là không giới hạn.

Trần CỨNG theo đặt chỗ: chi phí thật chỉ được ghi SAU khi model trả lời, nên nếu chỉ so với
chi phí đã ghi thì N request đồng thời ở sát trần đều lọt qua. Mỗi request vì vậy phải đặt
trước một khoản ước tính (`LLM_BUDGET_RESERVE_USD`) trong cùng giao dịch có khoá với phép
kiểm tra:

    chấp nhận  ⇔  đã_chi + đặt_chỗ_đang_hiệu_lực (kể cả của request này) ≤ trần

Đặt chỗ được trả lại khi request kết thúc (chi phí thật lúc đó đã nằm trong bảng usage), và
tự hết hạn sau `LLM_BUDGET_RESERVATION_TTL_SEC` nếu tiến trình chết giữa chừng. Vượt trần chỉ
còn có thể xảy ra khi chi phí thật của MỘT request lớn hơn khoản đặt trước của chính nó.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, text
from sqlalchemy.orm import Session, sessionmaker

from costs.models import LlmBudgetReservation, LlmUsageEvent

logger = logging.getLogger(__name__)
_PG_LOCK_KEY = 0x46_4F_42_55  # "FOBU": khoá advisory dùng chung cho mọi lượt đặt chỗ


def _limit(name: str) -> float:
    try:
        value = float(os.getenv(name, "0") or 0)
    except ValueError:
        return 0.0
    return value if value > 0 else 0.0


def daily_limits() -> tuple[float, float]:
    return _limit("LLM_DAILY_BUDGET_USD"), _limit("LLM_USER_DAILY_BUDGET_USD")


def reserve_amount() -> float:
    """Khoản đặt trước cho một request hỏi đáp = chi phí TỐI ĐA của nó.

    llm.py từ chối prompt quá LLM_MAX_INPUT_TOKENS và giới hạn đầu ra ở LLM_MAX_OUTPUT_TOKENS,
    nên chi phí sinh câu trả lời không thể vượt con số này; cộng thêm một lượt Cohere rerank khi
    được bật. Đặt LLM_BUDGET_RESERVE_USD để ghi đè.
    """
    configured = _limit("LLM_BUDGET_RESERVE_USD")
    if configured:
        return configured
    from config import COHERE_API_KEY, COHERE_RERANK_MODEL, LLM_MAX_INPUT_TOKENS, LLM_MAX_OUTPUT_TOKENS, LLM_MODEL
    from costs.pricing import compute_cost

    amount = compute_cost(LLM_MODEL, LLM_MAX_INPUT_TOKENS, LLM_MAX_OUTPUT_TOKENS)
    if COHERE_API_KEY:
        amount += compute_cost(COHERE_RERANK_MODEL, units=1)
    return max(amount, 0.001)


def ingest_reserve_amount(content_bytes: int) -> float:
    """Đặt trước cho một lần nạp tài liệu: embedding tối đa = số byte × MAX_TEXT_CHARS_PER_BYTE
    ký tự (ingest từ chối văn bản dài hơn: DOCX/PDF nén nên văn bản CÓ THỂ dài hơn tệp gốc),
    cộng một lượt OCR có trần đầu ra cho ảnh."""
    from config import EMBEDDING_MODEL, LLM_MAX_OUTPUT_TOKENS, LLM_MODEL
    from costs.pricing import compute_cost
    from costs.recorder import CHARS_PER_TOKEN
    from config import MAX_TEXT_CHARS_PER_BYTE

    max_chars = content_bytes * MAX_TEXT_CHARS_PER_BYTE
    embedding = compute_cost(EMBEDDING_MODEL, max(1, int(max_chars / CHARS_PER_TOKEN)))
    ocr = compute_cost(LLM_MODEL, 1_000, LLM_MAX_OUTPUT_TOKENS)
    return max(embedding + ocr, 0.001)


def _ttl() -> timedelta:
    try:
        seconds = int(os.getenv("LLM_BUDGET_RESERVATION_TTL_SEC", "900"))
    except ValueError:
        seconds = 900
    return timedelta(seconds=max(seconds, 60))


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


def reserved_now(db: Session, user_id: int | None = None) -> float:
    query = db.query(func.coalesce(func.sum(LlmBudgetReservation.amount_usd), 0.0)).filter(
        LlmBudgetReservation.expires_at > datetime.now(timezone.utc))
    if user_id is not None:
        query = query.filter(LlmBudgetReservation.user_id == user_id)
    return float(query.scalar() or 0.0)


class BudgetReservation:
    """Khoản đặt trước của một request. `release()` an toàn khi gọi nhiều lần."""

    def __init__(self, reservation_id: int | None = None, factory: sessionmaker | None = None):
        self.id = reservation_id
        self._factory = factory

    def release(self) -> None:
        if self.id is None or self._factory is None:
            return
        reservation_id, self.id = self.id, None
        try:
            with self._factory() as db:
                db.execute(delete(LlmBudgetReservation).where(LlmBudgetReservation.id == reservation_id))
                db.commit()
        except Exception as exc:  # hết hạn tự nhiên theo TTL nếu không xoá được
            logger.warning("Could not release budget reservation (%s)", type(exc).__name__)

    async def release_async(self) -> None:
        # DELETE cần khoá ghi SQLite (chờ tới busy_timeout 30 s): không chạy trên event loop.
        # Che chắn khỏi huỷ: client ngắt kết nối huỷ cả scope, mà khoản đặt trước vẫn phải trả.
        if self.id is None:
            return
        import anyio
        from starlette.concurrency import run_in_threadpool

        with anyio.CancelScope(shield=True):
            await run_in_threadpool(self.release)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.release_async()


def enforce_budget(db: Session, user_id: int | None, amount: float | None = None) -> BudgetReservation:
    """Đặt chỗ ngân sách cho một request AI, hoặc raise 429 nếu không còn đủ.

    `amount`: chi phí tối đa của request (mặc định reserve_amount() cho một lượt hỏi đáp).
    Gọi trước mọi thay đổi chưa commit của request: hàm tự commit giao dịch đặt chỗ.
    """
    global_limit, user_limit = daily_limits()
    if not global_limit and not user_limit:
        return BudgetReservation()
    now = datetime.now(timezone.utc)
    amount = amount if amount is not None else reserve_amount()
    bind = db.get_bind()
    try:
        if bind.dialect.name == "postgresql":
            # READ COMMITTED: hai giao dịch không thấy đặt chỗ chưa commit của nhau -> tuần tự hoá.
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PG_LOCK_KEY})
        # Trên SQLite, INSERT lấy khoá ghi của cả DB trước khi đọc tổng bên dưới.
        db.execute(delete(LlmBudgetReservation).where(LlmBudgetReservation.expires_at <= now))
        row = LlmBudgetReservation(user_id=user_id, amount_usd=amount, created_at=now, expires_at=now + _ttl())
        db.add(row)
        db.flush()
        if global_limit and spent_today(db) + reserved_now(db) > global_limit:
            db.rollback()
            raise HTTPException(
                status_code=429,
                detail="Hệ thống đã dùng hết ngân sách AI hôm nay. Vui lòng thử lại vào ngày mai.",
                headers={"Retry-After": "3600"},
            )
        if user_limit and user_id is not None and spent_today(db, user_id) + reserved_now(db, user_id) > user_limit:
            db.rollback()
            raise HTTPException(
                status_code=429,
                detail="Bạn đã dùng hết hạn mức AI hôm nay.",
                headers={"Retry-After": "3600"},
            )
        reservation_id = row.id
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    return BudgetReservation(reservation_id, sessionmaker(bind=bind, autoflush=False))


async def enforce_budget_async(db: Session, user_id: int | None, amount: float | None = None) -> BudgetReservation:
    """enforce_budget chạy trong threadpool cho handler `async`.

    Đặt chỗ là DELETE + INSERT dưới khoá ghi (SQLite busy_timeout 30 s; container pipeline cùng
    ghi app.db). Gọi thẳng trong handler async thì một lần chờ khoá đóng băng cả worker uvicorn,
    kể cả /health. Session vẫn chỉ được dùng tuần tự (handler chờ kết quả), nên an toàn.
    """
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(enforce_budget, db, user_id, amount)


def budgeted_stream(content, reservation: BudgetReservation, **kwargs):
    """StreamingResponse luôn trả khoản đặt trước khi response kết thúc, kể cả khi client ngắt
    TRƯỚC lần lặp đầu: lúc đó generator chưa chạy nên `finally` của nó không bao giờ chạy, và
    khoản đặt trước bị giữ tới hết TTL 15 phút, chặn oan các lượt hỏi khác của user.
    """
    from starlette.responses import StreamingResponse

    class _Response(StreamingResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                await reservation.release_async()

    return _Response(content, **kwargs)
