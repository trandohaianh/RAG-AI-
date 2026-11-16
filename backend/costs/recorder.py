"""Ghi nhận mỗi lần gọi AI: Prometheus counter + 1 dòng trong llm_usage_events.

Ghi chi phí là best-effort: lỗi DB không bao giờ làm hỏng câu trả lời cho người dùng.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Callable

from sqlalchemy.orm import Session

from costs.context import current
from costs.pricing import compute_cost

logger = logging.getLogger(__name__)

_session_factory: Callable[[], Session] | None = None


def enabled() -> bool:
    return os.getenv("COST_TRACKING_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    """Cho test/ script trỏ recorder sang DB khác."""
    global _session_factory
    _session_factory = factory


def _factory() -> Callable[[], Session]:
    if _session_factory is not None:
        return _session_factory
    from database import SessionLocal

    return SessionLocal


def estimate_tokens(text: str) -> int:
    """Ước lượng token khi provider không trả usage (≈ 4 ký tự / token)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def record_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    thinking_tokens: int = 0,
    units: float = 0.0,
    estimated: bool = False,
    latency_ms: int | None = None,
    status: str = "ok",
    feature: str | None = None,
    user_id: int | None = None,
    workspace_id: int | None = None,
) -> float:
    """Trả về chi phí USD đã tính (kể cả khi không ghi được DB)."""
    ctx = current()
    feature = feature or ctx.feature or "other"
    user_id = user_id if user_id is not None else ctx.user_id
    workspace_id = workspace_id if workspace_id is not None else ctx.workspace_id
    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    thinking_tokens = max(0, int(thinking_tokens or 0))
    cost = compute_cost(model, input_tokens, output_tokens + thinking_tokens, units)

    try:
        from metrics import LLM_COST_USD, LLM_LATENCY, LLM_REQUESTS, LLM_TOKENS

        labels = {"provider": provider, "model": model, "feature": feature}
        LLM_REQUESTS.labels(status=status, **labels).inc()
        if input_tokens:
            LLM_TOKENS.labels(kind="input", **labels).inc(input_tokens)
        if output_tokens:
            LLM_TOKENS.labels(kind="output", **labels).inc(output_tokens)
        if thinking_tokens:
            LLM_TOKENS.labels(kind="thinking", **labels).inc(thinking_tokens)
        if cost:
            LLM_COST_USD.labels(**labels).inc(cost)
        if latency_ms is not None:
            LLM_LATENCY.labels(provider=provider, feature=feature).observe(latency_ms / 1000)
    except Exception as exc:  # pragma: no cover - metrics never block a request
        logger.debug("Cost metrics unavailable (%s)", type(exc).__name__)

    if not enabled():
        return cost
    try:
        from costs.models import LlmUsageEvent

        with _factory()() as db:
            db.add(LlmUsageEvent(
                user_id=user_id, workspace_id=workspace_id, feature=feature[:40],
                provider=provider[:20], model=(model or "unknown")[:80],
                input_tokens=input_tokens, output_tokens=output_tokens,
                thinking_tokens=thinking_tokens, units=float(units or 0.0),
                estimated=bool(estimated), cost_usd=cost, latency_ms=latency_ms,
                status=status[:12],
            ))
            db.commit()
    except Exception as exc:
        logger.warning("Could not persist LLM usage (%s)", type(exc).__name__)
    return cost


def gemini_usage(response) -> tuple[int, int, int]:
    """(input, output, thinking) từ usage_metadata của google-genai; thiếu thì 0."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return 0, 0, 0

    def _int(name: str) -> int:
        value = getattr(meta, name, None)
        return int(value) if isinstance(value, (int, float)) else 0

    return _int("prompt_token_count"), _int("candidates_token_count"), _int("thoughts_token_count")
