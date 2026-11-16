"""Tích hợp CRM (ticket khách hàng) và NMS (cảnh báo mạng) với ca hiện trường.

VÀO — POST /public/field/integrations/{public_id}/events
    Header  X-FieldOps-Timestamp: <unix giây>
            X-FieldOps-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
    Body    {"event_id": "...", "type": "ticket.created|ticket.updated|ticket.closed|alarm.raised|alarm.cleared",
             "external_ref": "TCK-1234", "data": {"title", "description", "customer", "address", "latitude",
             "longitude", "priority"|"severity", "device_model", "serial_number", "window_start", "window_end"}}
    - Chữ ký sai/lệch giờ > 5 phút -> 401; cùng event_id gửi lại -> 200 duplicate (không tạo trùng).
    - created/raised: tạo ca mới (hoặc cập nhật nếu external_ref đã có); updated: cập nhật trường;
      ticket.closed: đóng ca; alarm.cleared: ghi sự kiện, chỉ tự đóng nếu chưa ai bắt đầu xử lý.

RA — khi ca có nguồn từ một kết nối đổi trạng thái/người phụ trách/lịch: một bản ghi outbox
    (field_webhook_deliveries) được gửi tới outbound_url với cùng kiểu chữ ký. Lỗi -> thử lại
    với backoff 30s·2^n (tối đa 6 giờ), sau 8 lần -> dead-letter (xem và gửi lại trong giao diện).
    URL ra phải là https và không trỏ vào mạng nội bộ (chống SSRF).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from field.models import (
    FieldIntegration,
    FieldIntegrationEvent,
    FieldWebhookDelivery,
    FieldWorkspaceMember,
    ServiceCase,
    ServiceCaseEvent,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/field", tags=["field integrations"])
public_router = APIRouter(prefix="/public/field", tags=["field integrations (public)"])

MAX_SKEW_SECONDS = 300
MAX_ATTEMPTS = 8
EVENT_TYPES = {"ticket.created", "ticket.updated", "ticket.closed", "alarm.raised", "alarm.cleared"}
SEVERITY = {"critical": "urgent", "major": "high", "minor": "normal", "warning": "low", "info": "low",
            "urgent": "urgent", "high": "high", "normal": "normal", "low": "low"}
_rate: dict[int, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


# ----------------------------------------------------------------------------- signing / SSRF
def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _allow_private() -> bool:
    return os.getenv("FIELD_WEBHOOK_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")


def validate_outbound_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (_allow_private() and parsed.scheme == "http"):
        raise ValueError("URL webhook phải dùng https")
    if not parsed.hostname:
        raise ValueError("URL webhook không hợp lệ")
    if _allow_private():
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("Không phân giải được tên miền webhook") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("URL webhook không được trỏ vào mạng nội bộ")


# ----------------------------------------------------------------------------- management API
class IntegrationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: str = Field(default="crm", pattern="^(crm|nms)$")
    outbound_url: str | None = Field(default=None, max_length=500)
    default_assignee: int | None = None


class IntegrationUpdate(BaseModel):
    outbound_url: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    default_assignee: int | None = None


def _owner_member(db: Session, workspace_id: int, user_id: int) -> FieldWorkspaceMember:
    member = db.scalar(select(FieldWorkspaceMember).where(
        FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id))
    if not member:
        raise HTTPException(status_code=404, detail="Không tìm thấy đội")
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Chỉ trưởng đội quản lý kết nối")
    return member


def _integration_for_owner(db: Session, integration_id: int, user_id: int) -> FieldIntegration:
    integration = db.get(FieldIntegration, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Không tìm thấy kết nối")
    _owner_member(db, integration.workspace_id, user_id)
    return integration


def _inbound_url(integration: FieldIntegration) -> str:
    from config import API_PUBLIC_URL

    return f"{API_PUBLIC_URL.rstrip('/')}/public/field/integrations/{integration.public_id}/events"


def _out(db: Session, integration: FieldIntegration, secret: str | None = None) -> dict:
    counts = defaultdict(int)
    for status in db.scalars(select(FieldWebhookDelivery.status).where(
            FieldWebhookDelivery.integration_id == integration.id)).all():
        counts[status] += 1
    last_in = db.scalar(select(FieldIntegrationEvent.received_at).where(
        FieldIntegrationEvent.integration_id == integration.id).order_by(FieldIntegrationEvent.id.desc()).limit(1))
    out = {"id": integration.id, "name": integration.name, "kind": integration.kind,
           "inbound_url": _inbound_url(integration), "outbound_url": integration.outbound_url,
           "default_assignee": integration.default_assignee, "is_active": integration.is_active,
           "deliveries": dict(counts), "last_inbound_at": last_in}
    if secret:
        out["secret"] = secret          # chỉ trả MỘT lần khi tạo / xoay khoá
    return out


def _check_assignee(db: Session, workspace_id: int, user_id: int | None) -> None:
    if user_id is not None and not db.scalar(select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id)):
        raise HTTPException(status_code=422, detail="Người nhận mặc định không thuộc đội")


@router.post("/workspaces/{workspace_id}/integrations", status_code=201)
def create_integration(workspace_id: int, body: IntegrationCreate, user: CurrentUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    _owner_member(db, workspace_id, user.id)
    if body.outbound_url:
        try:
            validate_outbound_url(body.outbound_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _check_assignee(db, workspace_id, body.default_assignee)
    secret = secrets.token_urlsafe(32)
    integration = FieldIntegration(workspace_id=workspace_id, name=body.name.strip(), kind=body.kind,
                                   public_id=secrets.token_urlsafe(18), secret=secret,
                                   outbound_url=body.outbound_url, default_assignee=body.default_assignee,
                                   created_by=user.id)
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return _out(db, integration, secret)


@router.get("/workspaces/{workspace_id}/integrations")
def list_integrations(workspace_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner_member(db, workspace_id, user.id)
    rows = db.scalars(select(FieldIntegration).where(FieldIntegration.workspace_id == workspace_id)
                      .order_by(FieldIntegration.id)).all()
    return [_out(db, row) for row in rows]


@router.patch("/integrations/{integration_id}")
def update_integration(integration_id: int, body: IntegrationUpdate, user: CurrentUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    integration = _integration_for_owner(db, integration_id, user.id)
    if "outbound_url" in body.model_fields_set:
        if body.outbound_url:
            try:
                validate_outbound_url(body.outbound_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        integration.outbound_url = body.outbound_url or None
    if body.is_active is not None:
        integration.is_active = body.is_active
    if "default_assignee" in body.model_fields_set:
        _check_assignee(db, integration.workspace_id, body.default_assignee)
        integration.default_assignee = body.default_assignee
    db.commit()
    return _out(db, integration)


@router.post("/integrations/{integration_id}/rotate-secret")
def rotate_secret(integration_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    integration = _integration_for_owner(db, integration_id, user.id)
    integration.secret = secrets.token_urlsafe(32)
    db.commit()
    return _out(db, integration, integration.secret)


@router.get("/integrations/{integration_id}/deliveries")
def list_deliveries(integration_id: int, status: str | None = None, user: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    _integration_for_owner(db, integration_id, user.id)
    query = select(FieldWebhookDelivery).where(FieldWebhookDelivery.integration_id == integration_id)
    if status:
        query = query.where(FieldWebhookDelivery.status == status)
    rows = db.scalars(query.order_by(FieldWebhookDelivery.id.desc()).limit(100)).all()
    return [{"id": r.id, "event_type": r.event_type, "status": r.status, "attempts": r.attempts,
             "next_attempt_at": r.next_attempt_at, "last_error": r.last_error, "response_code": r.response_code,
             "created_at": r.created_at, "delivered_at": r.delivered_at} for r in rows]


@router.post("/integrations/{integration_id}/deliveries/{delivery_id}/retry")
def retry_delivery(integration_id: int, delivery_id: int, user: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _integration_for_owner(db, integration_id, user.id)
    delivery = db.get(FieldWebhookDelivery, delivery_id)
    if not delivery or delivery.integration_id != integration_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt gửi")
    delivery.status, delivery.attempts = "pending", 0
    delivery.next_attempt_at = datetime.now(timezone.utc)
    db.commit()
    kick()
    return {"id": delivery.id, "status": delivery.status}


# ----------------------------------------------------------------------------- inbound
def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _rate_ok(integration_id: int, limit: int = 120, window: float = 60.0) -> bool:
    now = time.monotonic()
    with _rate_lock:
        q = _rate[integration_id]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _apply_fields(case: ServiceCase, data: dict) -> list[str]:
    changed = []
    mapping = {"title": ("title", 255), "description": ("symptom", 5000), "customer": ("customer_label", 160),
               "address": ("address", 500), "device_model": ("device_model", 160),
               "serial_number": ("serial_number", 160)}
    for key, (attr, size) in mapping.items():
        if data.get(key) not in (None, "") and str(data[key])[:size] != getattr(case, attr):
            setattr(case, attr, str(data[key])[:size])
            changed.append(attr)
    lat, lng = data.get("latitude"), data.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and -90 <= lat <= 90 and -180 <= lng <= 180:
        case.latitude, case.longitude = float(lat), float(lng)
        changed.append("location")
    level = SEVERITY.get(str(data.get("priority") or data.get("severity") or "").lower())
    if level and level != case.priority:
        case.priority = level
        changed.append("priority")
    for key in ("window_start", "window_end"):
        moment = _parse_time(data.get(key))
        if moment:
            setattr(case, key, moment)
            changed.append(key)
    return changed


@public_router.post("/integrations/{public_id}/events")
async def inbound_event(public_id: str, request: Request, db: Session = Depends(get_db)):
    from metrics import FIELD_WEBHOOKS

    integration = db.scalar(select(FieldIntegration).where(FieldIntegration.public_id == public_id))
    if not integration or not integration.is_active:
        FIELD_WEBHOOKS.labels("inbound", "rejected").inc()
        raise HTTPException(status_code=404, detail="Unknown integration")
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise HTTPException(status_code=413, detail="Payload too large")
    timestamp = request.headers.get("x-fieldops-timestamp", "")
    signature = request.headers.get("x-fieldops-signature", "")
    try:
        skew = abs(time.time() - int(timestamp))
    except ValueError:
        skew = MAX_SKEW_SECONDS + 1
    if skew > MAX_SKEW_SECONDS or not hmac.compare_digest(signature, sign(integration.secret, timestamp, raw)):
        FIELD_WEBHOOKS.labels("inbound", "rejected").inc()
        raise HTTPException(status_code=401, detail="Invalid signature or timestamp")
    if not _rate_ok(integration.id):
        raise HTTPException(status_code=429, detail="Too many events")
    try:
        payload = json.loads(raw)
        event_id, event_type = str(payload["event_id"])[:128], str(payload["type"])
        external_ref, data = str(payload["external_ref"])[:128], payload.get("data") or {}
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="Body must have event_id, type, external_ref, data")
    if event_type not in EVENT_TYPES or not isinstance(data, dict) or not event_id or not external_ref:
        raise HTTPException(status_code=422, detail=f"type must be one of {sorted(EVENT_TYPES)}")

    seen = db.scalar(select(FieldIntegrationEvent).where(
        FieldIntegrationEvent.integration_id == integration.id, FieldIntegrationEvent.event_id == event_id))
    if seen:
        FIELD_WEBHOOKS.labels("inbound", "duplicate").inc()
        return {"duplicate": True, "case_id": seen.case_id, "outcome": seen.outcome}

    source = f"{integration.kind}:{integration.id}"
    case = db.scalar(select(ServiceCase).where(ServiceCase.workspace_id == integration.workspace_id,
                                               ServiceCase.external_source == source,
                                               ServiceCase.external_ref == external_ref))
    actor = integration.created_by
    outcome = "ignored"
    if case is None and event_type in ("ticket.created", "alarm.raised", "ticket.updated"):
        title = str(data.get("title") or "").strip()
        symptom = str(data.get("description") or "").strip()
        if len(title) < 3 or len(symptom) < 3:
            raise HTTPException(status_code=422, detail="data.title and data.description are required to open a case")
        case = ServiceCase(workspace_id=integration.workspace_id, created_by=actor,
                           assigned_to=integration.default_assignee, title=title[:255], symptom=symptom[:5000],
                           external_source=source, external_ref=external_ref)
        _apply_fields(case, data)
        db.add(case)
        db.flush()
        label = "ticket CRM" if integration.kind == "crm" else "cảnh báo NMS"
        db.add(ServiceCaseEvent(case_id=case.id, actor_id=actor, event_type="created",
                                detail=f"Tạo từ {label} {external_ref} ({integration.name})"))
        outcome = "created"
    elif case is not None and event_type in ("ticket.created", "alarm.raised", "ticket.updated"):
        changed = _apply_fields(case, data)
        if changed:
            db.add(ServiceCaseEvent(case_id=case.id, actor_id=actor, event_type="external",
                                    detail=f"{integration.name} cập nhật: {', '.join(changed)}"))
        outcome = "updated" if changed else "unchanged"
    elif case is not None and event_type == "ticket.closed":
        if case.status != "resolved":
            case.status = "resolved"
            db.add(ServiceCaseEvent(case_id=case.id, actor_id=actor, event_type="status",
                                    detail=f"Đóng theo ticket {external_ref} trên {integration.name}"))
        outcome = "closed"
    elif case is not None and event_type == "alarm.cleared":
        note = f"Cảnh báo {external_ref} đã hết trên {integration.name}"
        if case.status == "open":
            case.status = "resolved"
            note += " — ca chưa ai xử lý nên được đóng tự động"
        db.add(ServiceCaseEvent(case_id=case.id, actor_id=actor, event_type="external", detail=note))
        outcome = "cleared"
    db.add(FieldIntegrationEvent(integration_id=integration.id, event_id=event_id, event_type=event_type,
                                 case_id=case.id if case else None, outcome=outcome))
    try:
        db.commit()
    except IntegrityError:          # cùng event_id đến song song: bản kia đã thắng
        db.rollback()
        FIELD_WEBHOOKS.labels("inbound", "duplicate").inc()
        return {"duplicate": True}
    FIELD_WEBHOOKS.labels("inbound", "accepted").inc()
    return {"duplicate": False, "case_id": case.id if case else None, "outcome": outcome}


# ----------------------------------------------------------------------------- outbound
def enqueue_case_event(db: Session, case: ServiceCase, event_type: str, extra: dict | None = None) -> int:
    """Gọi TRƯỚC commit (cùng transaction với thay đổi ca) -> outbox không bao giờ lệch dữ liệu."""
    if not case.external_source or ":" not in case.external_source:
        return 0
    try:
        integration_id = int(case.external_source.split(":", 1)[1])
    except ValueError:
        return 0
    integration = db.get(FieldIntegration, integration_id)
    if not integration or not integration.is_active or not integration.outbound_url:
        return 0
    payload = {"event_id": uuid.uuid4().hex, "type": event_type,
               "occurred_at": datetime.now(timezone.utc).isoformat(),
               "case": {"id": case.id, "external_ref": case.external_ref, "status": case.status,
                        "assigned_to": case.assigned_to, "title": case.title,
                        "scheduled_start": case.scheduled_start.isoformat() if case.scheduled_start else None},
               **(extra or {})}
    db.add(FieldWebhookDelivery(integration_id=integration.id, event_type=event_type,
                                payload_json=json.dumps(payload, ensure_ascii=False),
                                next_attempt_at=datetime.now(timezone.utc)))
    return 1


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** attempts, 6 * 3600))


def dispatch_due(db: Session, *, now: datetime | None = None, client=None, limit: int = 50) -> dict:
    import httpx

    from metrics import FIELD_WEBHOOKS

    now = now or datetime.now(timezone.utc)
    rows = db.scalars(select(FieldWebhookDelivery).where(
        FieldWebhookDelivery.status == "pending", FieldWebhookDelivery.next_attempt_at <= now)
        .order_by(FieldWebhookDelivery.id).limit(limit)).all()
    summary = defaultdict(int)
    own_client = client is None
    client = client or httpx.Client(timeout=10.0, follow_redirects=False)
    try:
        for delivery in rows:
            integration = db.get(FieldIntegration, delivery.integration_id)
            if not integration or not integration.is_active or not integration.outbound_url:
                delivery.status, delivery.last_error = "dead", "kết nối đã tắt hoặc không còn URL"
                summary["dead"] += 1
                continue
            body = delivery.payload_json.encode()
            timestamp = str(int(time.time()))
            delivery.attempts += 1
            try:
                validate_outbound_url(integration.outbound_url)
                response = client.post(integration.outbound_url, content=body, headers={
                    "Content-Type": "application/json", "X-FieldOps-Timestamp": timestamp,
                    "X-FieldOps-Signature": sign(integration.secret, timestamp, body),
                    "X-FieldOps-Event": delivery.event_type})
                delivery.response_code = response.status_code
                ok = 200 <= response.status_code < 300
                error = None if ok else f"HTTP {response.status_code}"
            except Exception as exc:          # mạng / DNS / SSRF bị chặn
                ok, error = False, f"{type(exc).__name__}: {str(exc)[:200]}"
            if ok:
                delivery.status, delivery.delivered_at, delivery.last_error = "delivered", now, None
                summary["delivered"] += 1
                FIELD_WEBHOOKS.labels("outbound", "delivered").inc()
            elif delivery.attempts >= MAX_ATTEMPTS:
                delivery.status, delivery.last_error = "dead", error
                summary["dead"] += 1
                FIELD_WEBHOOKS.labels("outbound", "dead").inc()
            else:
                delivery.next_attempt_at, delivery.last_error = now + _backoff(delivery.attempts), error
                summary["retry"] += 1
                FIELD_WEBHOOKS.labels("outbound", "retry").inc()
        db.commit()
    finally:
        if own_client:
            client.close()
    return dict(summary)


_kick_lock = threading.Lock()


def kick() -> None:
    """Gửi ngay (luồng nền) thay vì đợi worker pipeline quét 30 giây/lần."""
    if os.getenv("FIELD_WEBHOOK_INLINE", "1").lower() in ("0", "false", "no"):
        return
    if not _kick_lock.acquire(blocking=False):
        return

    def run():
        try:
            from database import SessionLocal

            with SessionLocal() as db:
                dispatch_due(db)
        except Exception:
            logger.exception("inline webhook dispatch failed")
        finally:
            _kick_lock.release()

    threading.Thread(target=run, name="webhook-kick", daemon=True).start()


def purge_old_records(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    events = db.execute(delete(FieldIntegrationEvent).where(
        FieldIntegrationEvent.received_at < now - timedelta(days=90))).rowcount
    delivered = db.execute(delete(FieldWebhookDelivery).where(
        FieldWebhookDelivery.status == "delivered", FieldWebhookDelivery.created_at < now - timedelta(days=30))).rowcount
    dead = db.execute(delete(FieldWebhookDelivery).where(
        FieldWebhookDelivery.status == "dead", FieldWebhookDelivery.created_at < now - timedelta(days=90))).rowcount
    db.commit()
    return {"integration_events_deleted": events or 0, "webhook_deliveries_deleted": (delivered or 0) + (dead or 0)}
