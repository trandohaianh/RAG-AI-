"""Bảng giá model (USD) và hàm quy đổi token -> tiền.

Giá niêm yết paid tier, kiểm tra ngày 2026-11-16 (ai.google.dev/pricing, cohere.com/pricing).
Gemini free tier không tính tiền thật; khi LLM_BILLING_TIER=free, chi phí hiển thị là
"giá trị quy đổi" để biết nếu lên paid tier sẽ tốn bao nhiêu.

Ghi đè/ bổ sung bằng biến môi trường LLM_PRICING_JSON, ví dụ:
  {"gemini-3-flash": {"input": 0.5, "output": 3.0}}
Đơn vị: input/output = USD trên 1 triệu token; unit = USD trên 1 đơn vị (lượt rerank...).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PRICING_CHECKED_AT = "2026-11-16"

# Thứ tự quan trọng: tên dài/cụ thể hơn phải đứng trước (khớp theo tiền tố).
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-3-flash": {"input": 0.50, "output": 3.00},
    "gemini-3-pro": {"input": 2.00, "output": 12.00},
    "gemini-embedding-001": {"input": 0.15, "output": 0.0},
    "text-embedding-004": {"input": 0.0, "output": 0.0},
    # Cohere Rerank: tính theo lượt search (mỗi lượt tối đa 100 tài liệu).
    "rerank-": {"unit": 2.00 / 1000},
    # Model chạy local: không mất tiền API (chi phí nằm ở server).
    "ollama:": {"input": 0.0, "output": 0.0},
}


@dataclass(frozen=True)
class Price:
    input_per_m: float = 0.0
    output_per_m: float = 0.0
    per_unit: float = 0.0
    known: bool = True


def _load_prices() -> list[tuple[str, dict[str, float]]]:
    prices = dict(_DEFAULT_PRICES)
    raw = os.getenv("LLM_PRICING_JSON", "").strip()
    if raw:
        try:
            override = json.loads(raw)
            if not isinstance(override, dict):
                raise ValueError("LLM_PRICING_JSON must be an object")
            for name, spec in override.items():
                if not isinstance(spec, dict):
                    raise ValueError(f"Invalid price for {name}")
                prices[str(name)] = {k: float(v) for k, v in spec.items()}
        except (ValueError, TypeError) as exc:
            logger.warning("Ignoring invalid LLM_PRICING_JSON (%s)", exc)
    # Khớp tên dài trước để "gemini-2.5-flash-lite" không bị bắt bởi "gemini-2.5-flash".
    return sorted(prices.items(), key=lambda item: len(item[0]), reverse=True)


_PRICES = _load_prices()


def reload_prices() -> None:
    global _PRICES
    _PRICES = _load_prices()


def _normalize(model: str) -> str:
    name = (model or "").strip().lower()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return name


def price_for(model: str) -> Price:
    name = _normalize(model)
    for prefix, spec in _PRICES:
        if name.startswith(prefix.lower()):
            return Price(
                input_per_m=spec.get("input", 0.0),
                output_per_m=spec.get("output", 0.0),
                per_unit=spec.get("unit", 0.0),
            )
    return Price(known=False)


def compute_cost(model: str, input_tokens: int = 0, output_tokens: int = 0, units: float = 0.0) -> float:
    """Chi phí USD. Token suy nghĩ (thinking) của Gemini 2.5 tính như output."""
    price = price_for(model)
    cost = (
        max(0, input_tokens) * price.input_per_m / 1_000_000
        + max(0, output_tokens) * price.output_per_m / 1_000_000
        + max(0.0, units) * price.per_unit
    )
    return round(cost, 8)


def billing_tier() -> str:
    tier = os.getenv("LLM_BILLING_TIER", "free").strip().lower()
    return tier if tier in {"free", "paid"} else "free"


def price_table() -> list[dict]:
    return [
        {
            "model": name,
            "input_per_million": spec.get("input", 0.0),
            "output_per_million": spec.get("output", 0.0),
            "per_unit": spec.get("unit", 0.0),
        }
        for name, spec in sorted(_PRICES)
    ]
