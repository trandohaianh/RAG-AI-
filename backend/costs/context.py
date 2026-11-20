"""Ngữ cảnh gán chi phí: ai (user/workspace) và tính năng nào (chat, ingest...) đang gọi AI.

Dùng ContextVar nên tự đi theo asyncio.to_thread. Với threading.Thread hoặc
loop.run_in_executor (không copy context), bọc hàm bằng `bind_context`.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class UsageContext:
    user_id: int | None = None
    workspace_id: int | None = None
    feature: str = "other"
    request_id: str | None = None


# UsageContext là dataclass frozen (bất biến) nên dùng làm default an toàn.
_CTX: contextvars.ContextVar[UsageContext] = contextvars.ContextVar(
    "fieldops_usage_context",
    default=UsageContext(),  # noqa: B039
)


def current() -> UsageContext:
    return _CTX.get()


@contextmanager
def usage_scope(**changes) -> Iterator[UsageContext]:
    """Ghi đè một phần ngữ cảnh trong khối `with`; giá trị None giữ nguyên giá trị cũ."""
    base = _CTX.get()
    updates = {k: v for k, v in changes.items() if v is not None}
    token = _CTX.set(replace(base, **updates))
    try:
        yield _CTX.get()
    finally:
        _CTX.reset(token)


def bind_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Trả về hàm chạy trong bản sao context hiện tại (dùng cho thread/executor)."""
    ctx = contextvars.copy_context()

    def runner(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

    return runner


def set_request_usage(**changes) -> None:
    """Đặt ngữ cảnh cho phần còn lại của task hiện tại (mỗi request/stream là 1 task).

    Không reset: dùng ở đầu endpoint hoặc đầu generator SSE, nơi task kết thúc cùng request.
    """
    base = _CTX.get()
    updates = {k: v for k, v in changes.items() if v is not None}
    _CTX.set(replace(base, **updates))
