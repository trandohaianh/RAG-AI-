"""M15 — thông báo đẩy cho lịch hôm nay (docs/design/M15_thong_bao_day.md).

- App đăng ký Expo push token sau khi thợ bấm "Bật thông báo"; đăng xuất thì xoá.
- Giao ca / chuyển ca / lưu lịch ghi một dòng vào `field_push_outbox` trong CÙNG giao dịch.
- Luồng nền chờ ~20 giây rồi gom theo từng người (Q4) thành 1 thông báo và gửi qua Expo Push.
- Nội dung chỉ có số ca và giờ (Q2): không tên, địa chỉ, số điện thoại hay tiêu đề ca.
- Thông báo là phần thêm: lỗi ở đây không bao giờ làm hỏng việc giao ca hay xếp lịch.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from field.models import FieldPushDevice, FieldPushOutbox, ServiceCase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/field/push", tags=["field push"])

GROUP_SECONDS = 20  # Q4: gom các thay đổi trong khoảng này thành 1 thông báo / người
CLAIM_LEASE = timedelta(minutes=2)  # tiến trình chết giữa chừng thì dòng tự đến hạn lại
MAX_ATTEMPTS = 5
GIVE_UP_AFTER = timedelta(hours=24)  # lịch đã cũ, báo nữa chỉ gây rối
KEEP_DAYS = 7
EXPO_BATCH = 100  # giới hạn số thông báo mỗi lần gọi Expo
REQUEST_TIMEOUT_SECONDS = 10.0
ACTIVE_STATUSES = ("open", "in_progress", "waiting", "escalated")
TOKEN_RE = re.compile(r"^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{10,160}\]$")
Kind = Literal["assigned", "schedule_changed", "unassigned"]

# Cho test thay thế (không gọi Expo thật, không ngủ thật).
_transport: httpx.BaseTransport | None = None
_sleep = time.sleep


def enabled() -> bool:
    return bool(config.PUSH_ENABLED)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- API thiết bị
class DeviceIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    platform: Literal["android", "ios"] = "android"


class TokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.get("/status")
def push_status(user: CurrentUser = Depends(get_current_user)) -> dict:
    """App hỏi trước khi hiện thẻ "Bật thông báo": máy chủ tắt thì không xin quyền vô ích."""
    return {"enabled": enabled()}


@router.post("/devices", status_code=204)
def register_device(body: DeviceIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    token = body.token.strip()
    if not TOKEN_RE.match(token):
        raise HTTPException(status_code=422, detail="Mã nhận thông báo không hợp lệ")
    now = datetime.now(UTC)
    for _ in range(2):  # hai lần đăng ký cùng token song song: lần thua đi nhánh cập nhật
        device = db.scalar(select(FieldPushDevice).where(FieldPushDevice.token == token))
        if device:
            # Máy đổi người dùng: token chuyển sang người vừa đăng nhập, người cũ thôi nhận.
            device.user_id, device.platform, device.last_seen_at = user.id, body.platform, now
        else:
            db.add(FieldPushDevice(user_id=user.id, token=token, platform=body.platform, last_seen_at=now))
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
    return Response(status_code=204)


@router.post("/devices/unregister", status_code=204)
def unregister_device(body: TokenIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Gọi khi đăng xuất. Chỉ xoá token của chính mình."""
    db.execute(
        delete(FieldPushDevice).where(FieldPushDevice.token == body.token.strip(), FieldPushDevice.user_id == user.id)
    )
    db.commit()
    return Response(status_code=204)


# ----------------------------------------------------------------------------- ghi hàng đợi
def enqueue(
    db: Session, user_id: int | None, case_id: int, kind: Kind, *, actor_id: int | None, now: datetime | None = None
) -> int:
    """Gọi TRƯỚC commit, cùng giao dịch với thay đổi ca. Trả 1 nếu đã xếp hàng."""
    if not enabled() or user_id is None or user_id == actor_id:
        return 0  # tự giao cho chính mình thì không báo
    if db.scalar(select(FieldPushDevice.id).where(FieldPushDevice.user_id == user_id).limit(1)) is None:
        return 0  # người này chưa bật thông báo trên máy nào
    now = now or datetime.now(UTC)
    db.add(
        FieldPushOutbox(
            user_id=user_id,
            case_id=case_id,
            kind=kind,
            next_attempt_at=now + timedelta(seconds=GROUP_SECONDS),
            created_at=now,
        )
    )
    return 1


def enqueue_reassignment(
    db: Session, case_id: int, old: int | None, new: int | None, *, actor_id: int | None, now: datetime | None = None
) -> int:
    if old == new:
        return 0
    queued = 0
    if old is not None:
        queued += enqueue(db, old, case_id, "unassigned", actor_id=actor_id, now=now)
    if new is not None:
        queued += enqueue(db, new, case_id, "assigned", actor_id=actor_id, now=now)
    return queued


# ----------------------------------------------------------------------------- soạn nội dung
def _tz() -> timezone:
    return timezone(timedelta(minutes=config.FIELD_TZ_OFFSET_MINUTES))


def compose(db: Session, user_id: int, rows: list[FieldPushOutbox], now: datetime) -> dict | None:
    """Gom các dòng của một người thành 1 thông báo, theo trạng thái ca LÚC GỬI.

    Giao rồi chuyển đi trong cùng 20 giây thì không báo gì. Ca đã đóng thì bỏ qua.
    """
    kinds: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        kinds[row.case_id].add(row.kind)
    new: list[ServiceCase] = []
    changed: list[ServiceCase] = []
    moved = 0
    for case_id in sorted(kinds):
        case = db.get(ServiceCase, case_id)
        mine = case is not None and case.assigned_to == user_id and case.status in ACTIVE_STATUSES
        if mine:
            (new if "assigned" in kinds[case_id] else changed).append(case)
        elif "unassigned" in kinds[case_id] and "assigned" not in kinds[case_id]:
            moved += 1
    if not new and not changed and not moved:
        return None
    tz = _tz()
    today = now.astimezone(tz).date()

    def local(case: ServiceCase) -> datetime | None:
        return _aware(case.scheduled_start).astimezone(tz) if case.scheduled_start else None

    starts = sorted(s for s in (local(c) for c in new + changed) if s is not None)
    day: date | None = starts[0].date() if starts else None
    parts: list[str] = []
    if new:
        text = f"Bạn có {len(new)} ca mới."
        first_new = min((s for s in (local(c) for c in new) if s is not None), default=None)
        if first_new is not None and first_new.date() == day:
            text += f" Ca đầu bắt đầu {first_new:%H:%M}."
        parts.append(text)
    if changed:
        parts.append(f"Lịch của bạn vừa được cập nhật ({len(changed)} ca).")
    if moved:
        parts.append(f"{moved} ca đã được chuyển cho người khác.")
    if day is None:
        title = "FieldOps — lịch hôm nay" if not new else "FieldOps — ca mới"
    elif day == today:
        title = "FieldOps — lịch hôm nay"
    else:
        title = f"FieldOps — lịch ngày {day:%d/%m}"
    return {
        "title": title,
        "body": " ".join(parts),
        "data": {"screen": "today", "date": day.isoformat() if day else None},
    }


# ----------------------------------------------------------------------------- gửi
class PushHTTPError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _post(messages: list[dict]) -> list[dict]:
    """Gửi lên Expo Push. Chỉ gửi token + nội dung đã soạn ở compose()."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {config.EXPO_ACCESS_TOKEN}"
    tickets: list[dict] = []
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=_transport, headers=headers) as client:
        for start in range(0, len(messages), EXPO_BATCH):
            response = client.post(config.EXPO_PUSH_URL, json=messages[start : start + EXPO_BATCH])
            if response.status_code != 200:
                raise PushHTTPError(response.status_code)
            data = response.json().get("data")
            if not isinstance(data, list):
                raise PushHTTPError(502)
            tickets.extend(t if isinstance(t, dict) else {} for t in data)
    return tickets


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


def dispatch_due(db: Session, *, now: datetime | None = None) -> dict:
    """Gửi các thông báo đến hạn. Cả API (kick) lẫn vòng lặp pipeline đều gọi được an toàn."""
    now = now or datetime.now(UTC)
    summary: dict[str, int] = defaultdict(int)
    users = db.scalars(
        select(FieldPushOutbox.user_id)
        .where(FieldPushOutbox.status == "pending", FieldPushOutbox.next_attempt_at <= now)
        .distinct()
    ).all()
    for user_id in users:
        claim = uuid.uuid4().hex
        # Giành quyền gửi bằng UPDATE có điều kiện. Lấy luôn các dòng vừa xếp hàng (đến hạn
        # trong 20 giây tới) để mọi thay đổi gần nhau thành 1 thông báo.
        claimed = db.execute(
            update(FieldPushOutbox)
            .where(
                FieldPushOutbox.user_id == user_id,
                FieldPushOutbox.status == "pending",
                FieldPushOutbox.next_attempt_at <= now + timedelta(seconds=GROUP_SECONDS),
            )
            .values(claim=claim, next_attempt_at=now + CLAIM_LEASE)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if not claimed:
            summary["skipped_race"] += 1
            continue
        rows = db.scalars(select(FieldPushOutbox).where(FieldPushOutbox.claim == claim)).all()
        live = []
        for row in rows:
            if _aware(row.created_at) < now - GIVE_UP_AFTER:
                row.status, row.last_error = "dead", "quá 24 giờ, lịch đã cũ"
                summary["expired"] += 1
            else:
                live.append(row)
        message = compose(db, user_id, live, now) if live else None
        tokens = db.scalars(select(FieldPushDevice.token).where(FieldPushDevice.user_id == user_id)).all()
        if live and (message is None or not tokens):
            for row in live:
                row.status, row.sent_at = "skipped", now
            summary["skipped"] += 1
            live = []
        if live:
            payload = [
                {"to": token, "sound": "default", "channelId": "schedule", "priority": "high", **message}
                for token in tokens
            ]
            try:
                tickets = _post(payload)
                error = None
            except PushHTTPError as exc:
                tickets, error = None, exc
            except Exception as exc:  # mạng, timeout, JSON hỏng
                tickets, error = None, exc
            if error is None:
                for token, ticket in zip(tokens, tickets, strict=False):
                    details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
                    if ticket.get("status") == "error" and details.get("error") == "DeviceNotRegistered":
                        # Gỡ app hoặc tắt quyền: token không còn dùng được.
                        db.execute(delete(FieldPushDevice).where(FieldPushDevice.token == token))
                        summary["devices_removed"] += 1
                for row in live:
                    row.status, row.sent_at, row.last_error, row.claim = "sent", now, None, None
                summary["sent"] += 1
            else:
                status_code = getattr(error, "status_code", None)
                permanent = status_code is not None and 400 <= status_code < 500 and status_code != 429
                text = f"{type(error).__name__}: {str(error)[:200]}"
                for row in live:
                    row.attempts += 1
                    row.last_error = text
                    if permanent or row.attempts >= MAX_ATTEMPTS:
                        row.status = "dead"
                    else:
                        row.claim, row.next_attempt_at = None, now + _backoff(row.attempts)
                summary["dead" if permanent or live[0].attempts >= MAX_ATTEMPTS else "retry"] += 1
                logger.warning("push to user %s failed: %s", user_id, text)
        db.commit()
    return dict(summary)


# ----------------------------------------------------------------------------- luồng nền
_state_lock = threading.Lock()
_state = {"pending": False, "running": False}


def kick() -> None:
    """Gọi SAU commit. Luồng nền chờ GROUP_SECONDS rồi gửi; nhiều lần gọi gần nhau dùng chung."""
    if not enabled() or os.getenv("FIELD_PUSH_INLINE", "1").lower() in ("0", "false", "no"):
        return
    with _state_lock:
        _state["pending"] = True
        if _state["running"]:
            return
        _state["running"] = True
    threading.Thread(target=_worker, name="push-kick", daemon=True).start()


def _worker() -> None:
    try:
        while True:
            with _state_lock:
                if not _state["pending"]:
                    _state["running"] = False
                    return
                _state["pending"] = False
            _sleep(GROUP_SECONDS)
            try:
                from database import SessionLocal

                with SessionLocal() as db:
                    dispatch_due(db)
            except Exception:
                logger.exception("inline push dispatch failed")
    except BaseException:
        with _state_lock:
            _state["running"] = False
        raise


def purge_old(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    deleted = db.execute(
        delete(FieldPushOutbox).where(
            FieldPushOutbox.status != "pending", FieldPushOutbox.created_at < now - timedelta(days=KEEP_DAYS)
        )
    ).rowcount
    db.commit()
    return int(deleted or 0)
