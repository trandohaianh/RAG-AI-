"""Tích hợp CRM (ticket khách hàng) và NMS (cảnh báo mạng) với ca hiện trường.

VÀO — POST /public/field/integrations/{public_id}/events
    Header  X-FieldOps-Timestamp: <unix giây>
            X-FieldOps-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
    Body    {"event_id": "...", "type": "ticket.created|ticket.updated|ticket.closed|alarm.raised|alarm.cleared",
             "external_ref": "TCK-1234", "data": {"title", "description", "customer", "address", "latitude",
             "longitude", "priority"|"severity", "device_model", "serial_number", "window_start", "window_end"}}
    - Chữ ký sai/lệch giờ > 5 phút -> 401; cùng event_id gửi lại -> 200 duplicate (không tạo trùng).
    - F5-A (chủ dự án chọn 23/11): "updated_at" (ISO 8601, ở gốc body hoặc trong data) là thời điểm
      bản ghi đổi trên CRM/NMS. Sự kiện có updated_at CŨ HƠN bản đã áp dụng cho ca -> 200
      outcome "stale_ignored", không đổi gì (sự kiện đến trễ/sai thứ tự không ghi đè dữ liệu mới);
      bằng nhau -> xử lý lại, kết quả idempotent; không gửi updated_at -> xử lý như trước.
    - created/raised: tạo ca mới (hoặc cập nhật nếu external_ref đã có); updated: cập nhật trường;
      ticket.closed: đóng ca; alarm.cleared: ghi sự kiện, chỉ tự đóng nếu chưa ai bắt đầu xử lý.

RA — khi ca có nguồn từ một kết nối đổi trạng thái/người phụ trách/lịch: một bản ghi outbox
    (field_webhook_deliveries) được gửi tới outbound_url với cùng kiểu chữ ký. Lỗi -> thử lại
    với backoff 30s·2^(n-1) ±20% jitter (30 s, 1, 2, 4, 8, 16, 32 phút), sau 8 lần gửi ->
    dead-letter (xem và gửi lại trong giao diện). Tổng cửa sổ thử lại chỉ ~63,5 phút (±20%:
    ~51–76 phút) kể từ lần gửi đầu; đối tác sập lâu hơn thì phải gửi lại bằng tay (F4-A, chủ dự
    án chọn 23/11): GET .../deliveries?status=dead liệt kê, POST .../deliveries/{id}/retry gửi lại
    một lượt, POST .../deliveries/retry-dead gửi lại tất cả; gauge fieldops_field_webhook_deliveries
    {status="dead"} + cảnh báo trong monitoring/alerts báo khi có lượt dead mới.
    Lỗi 4xx vĩnh viễn (trừ 408/429) -> dead ngay.
    Mỗi lần gửi có hạn chót tổng WEBHOOK_DEADLINE_SECONDS và chỉ đọc tối đa 4 KB phản hồi.
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
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
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
CLAIM_LEASE = timedelta(minutes=5)
# Hạn chót cho CẢ một lần gửi (kết nối + gửi + đọc phản hồi). Timeout của httpx tính theo từng lần
# đọc, nên một receiver "nhỏ giọt" 1 byte/9 s giữ dispatcher mãi, quá CLAIM_LEASE thì bị gửi trùng.
WEBHOOK_DEADLINE_SECONDS = float(os.getenv("FIELD_WEBHOOK_DEADLINE_SECONDS", "20"))
MAX_RESPONSE_BYTES = 4096
MAX_INBOUND_BYTES = 256 * 1024
EVENT_TYPES = {"ticket.created", "ticket.updated", "ticket.closed", "alarm.raised", "alarm.cleared"}
SEVERITY = {
    "critical": "urgent",
    "major": "high",
    "minor": "normal",
    "warning": "low",
    "info": "low",
    "urgent": "urgent",
    "high": "high",
    "normal": "normal",
    "low": "low",
}
_rate: dict[int, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


# ----------------------------------------------------------------------------- signing / SSRF
def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _allow_private() -> bool:
    return os.getenv("FIELD_WEBHOOK_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")


def validate_outbound_url(url: str) -> list[str]:
    """Kiểm tra URL webhook ra; trả về các IP công khai đã kiểm (rỗng khi cho phép mạng nội bộ)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (_allow_private() and parsed.scheme == "http"):
        raise ValueError("URL webhook phải dùng https")
    if not parsed.hostname:
        raise ValueError("URL webhook không hợp lệ")
    if _allow_private():
        return []
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("Không phân giải được tên miền webhook") from exc
    checked: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        # is_private bỏ sót 100.64.0.0/10 (CGNAT, metadata của một số cloud) và 0.0.0.0;
        # chỉ cho phép địa chỉ định tuyến công khai.
        if (
            not ip.is_global
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URL webhook không được trỏ vào mạng nội bộ")
        if str(ip) not in checked:
            checked.append(str(ip))
    if not checked:
        raise ValueError("Không phân giải được tên miền webhook")
    return checked


def _post_pinned(client, url: str, ips: list[str], *, content: bytes, headers: dict):
    """POST tới đúng IP vừa kiểm tra (chống DNS rebinding).

    Nếu để httpx tự phân giải lại tên miền, một DNS TTL ngắn có thể trả IP công khai lúc kiểm
    tra rồi 127.0.0.1/169.254.169.254 lúc kết nối. Ở đây URL dùng IP, còn Host và SNI giữ tên
    miền gốc nên chứng chỉ TLS vẫn được xác minh theo tên miền.
    """
    if not ips:
        return client.post(url, content=content, headers=headers)
    parsed = urlparse(url)
    ip = ips[0]
    host = f"[{ip}]" if ":" in ip else ip
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    pinned = parsed._replace(netloc=netloc).geturl()
    host_header = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return client.post(
        pinned, content=content, headers={**headers, "Host": host_header}, extensions={"sni_hostname": parsed.hostname}
    )


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
    member = db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id
        )
    )
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
    for status in db.scalars(
        select(FieldWebhookDelivery.status).where(FieldWebhookDelivery.integration_id == integration.id)
    ).all():
        counts[status] += 1
    last_in = db.scalar(
        select(FieldIntegrationEvent.received_at)
        .where(FieldIntegrationEvent.integration_id == integration.id)
        .order_by(FieldIntegrationEvent.id.desc())
        .limit(1)
    )
    out = {
        "id": integration.id,
        "name": integration.name,
        "kind": integration.kind,
        "inbound_url": _inbound_url(integration),
        "outbound_url": integration.outbound_url,
        "default_assignee": integration.default_assignee,
        "is_active": integration.is_active,
        "deliveries": dict(counts),
        "last_inbound_at": last_in,
    }
    if secret:
        out["secret"] = secret  # chỉ trả MỘT lần khi tạo / xoay khoá
    return out


def _check_assignee(db: Session, workspace_id: int, user_id: int | None) -> None:
    if user_id is not None and not db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id
        )
    ):
        raise HTTPException(status_code=422, detail="Người nhận mặc định không thuộc đội")


@router.post("/workspaces/{workspace_id}/integrations", status_code=201)
def create_integration(
    workspace_id: int,
    body: IntegrationCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owner_member(db, workspace_id, user.id)
    if body.outbound_url:
        try:
            validate_outbound_url(body.outbound_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _check_assignee(db, workspace_id, body.default_assignee)
    secret = secrets.token_urlsafe(32)
    integration = FieldIntegration(
        workspace_id=workspace_id,
        name=body.name.strip(),
        kind=body.kind,
        public_id=secrets.token_urlsafe(18),
        secret=secret,
        outbound_url=body.outbound_url,
        default_assignee=body.default_assignee,
        created_by=user.id,
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)
    return _out(db, integration, secret)


@router.get("/workspaces/{workspace_id}/integrations")
def list_integrations(workspace_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner_member(db, workspace_id, user.id)
    rows = db.scalars(
        select(FieldIntegration).where(FieldIntegration.workspace_id == workspace_id).order_by(FieldIntegration.id)
    ).all()
    return [_out(db, row) for row in rows]


@router.patch("/integrations/{integration_id}")
def update_integration(
    integration_id: int,
    body: IntegrationUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
def list_deliveries(
    integration_id: int,
    status: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _integration_for_owner(db, integration_id, user.id)
    query = select(FieldWebhookDelivery).where(FieldWebhookDelivery.integration_id == integration_id)
    if status:
        query = query.where(FieldWebhookDelivery.status == status)
    rows = db.scalars(query.order_by(FieldWebhookDelivery.id.desc()).limit(100)).all()
    return [
        {
            "id": r.id,
            "event_type": r.event_type,
            "status": r.status,
            "attempts": r.attempts,
            "next_attempt_at": r.next_attempt_at,
            "last_error": r.last_error,
            "response_code": r.response_code,
            "created_at": r.created_at,
            "delivered_at": r.delivered_at,
        }
        for r in rows
    ]


def _require_deliverable(integration: FieldIntegration) -> None:
    """Gửi lại khi kết nối đang tắt / chưa có URL chỉ làm lượt gửi thành dead lần nữa ngay lập tức."""
    if not integration.is_active or not integration.outbound_url:
        raise HTTPException(status_code=409, detail="Kết nối đang tắt hoặc chưa có URL báo ngược — bật/điền URL trước")


@router.post("/integrations/{integration_id}/deliveries/{delivery_id}/retry")
def retry_delivery(
    integration_id: int, delivery_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    from metrics import FIELD_WEBHOOKS

    integration = _integration_for_owner(db, integration_id, user.id)
    delivery = db.get(FieldWebhookDelivery, delivery_id)
    if not delivery or delivery.integration_id != integration_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt gửi")
    if delivery.status == "delivered":
        # Gửi lại một sự kiện đã giao thành công sẽ tạo bản trùng phía CRM.
        raise HTTPException(status_code=409, detail="Lượt gửi này đã thành công, không cần gửi lại")
    _require_deliverable(integration)
    delivery.status, delivery.attempts = "pending", 0
    delivery.next_attempt_at = datetime.now(UTC)
    db.commit()
    FIELD_WEBHOOKS.labels("outbound", "manual_retry").inc()
    kick()
    return {"id": delivery.id, "status": delivery.status}


@router.post("/integrations/{integration_id}/deliveries/retry-dead")
def retry_dead_deliveries(
    integration_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    """F4-A: sau khi CRM sập quá cửa sổ ~1 giờ, đưa MỌI lượt dead của kết nối về hàng đợi một lần.

    Mỗi lượt được thêm một cửa sổ 8 lần thử mới. Thứ tự gửi giữ theo id (thứ tự sự kiện gốc).
    """
    from metrics import FIELD_WEBHOOKS

    integration = _integration_for_owner(db, integration_id, user.id)
    _require_deliverable(integration)
    requeued = db.execute(
        update(FieldWebhookDelivery)
        .where(FieldWebhookDelivery.integration_id == integration_id, FieldWebhookDelivery.status == "dead")
        .values(status="pending", attempts=0, next_attempt_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if requeued:
        FIELD_WEBHOOKS.labels("outbound", "manual_retry").inc(requeued)
        kick()
    return {"requeued": requeued or 0}


# ----------------------------------------------------------------------------- inbound
def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


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
    mapping = {
        "title": ("title", 255),
        "description": ("symptom", 5000),
        "customer": ("customer_label", 160),
        "address": ("address", 500),
        "device_model": ("device_model", 160),
        "serial_number": ("serial_number", 160),
    }
    for key, (attr, size) in mapping.items():
        if data.get(key) not in (None, "") and str(data[key])[:size] != getattr(case, attr):
            setattr(case, attr, str(data[key])[:size])
            changed.append(attr)
    lat, lng = data.get("latitude"), data.get("longitude")
    if (
        isinstance(lat, (int, float))
        and isinstance(lng, (int, float))
        and -90 <= lat <= 90
        and -180 <= lng <= 180
        and (case.latitude, case.longitude) != (float(lat), float(lng))
    ):
        case.latitude, case.longitude = float(lat), float(lng)
        changed.append("location")
    level = SEVERITY.get(str(data.get("priority") or data.get("severity") or "").lower())
    if level and level != case.priority:
        case.priority = level
        changed.append("priority")
    for key in ("window_start", "window_end"):
        moment = _parse_time(data.get(key))
        if moment and moment != _as_utc(getattr(case, key)):
            setattr(case, key, moment)
            changed.append(key)
    return changed


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite trả datetime không múi giờ (đã lưu UTC); so sánh với datetime có múi giờ cần chuẩn hoá."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@public_router.post("/integrations/{public_id}/events")
async def inbound_event(public_id: str, request: Request, db: Session = Depends(get_db)):
    from metrics import FIELD_WEBHOOKS

    integration = db.scalar(select(FieldIntegration).where(FieldIntegration.public_id == public_id))
    if not integration or not integration.is_active:
        FIELD_WEBHOOKS.labels("inbound", "rejected").inc()
        raise HTTPException(status_code=404, detail="Unknown integration")
    # Endpoint công khai (chỉ cần public_id): không đọc cả body vào RAM rồi mới kiểm kích thước.
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_INBOUND_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    buffer = bytearray()
    async for chunk in request.stream():
        buffer += chunk
        if len(buffer) > MAX_INBOUND_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
    raw = bytes(buffer)
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
        raise HTTPException(status_code=422, detail="Body must have event_id, type, external_ref, data") from None
    if event_type not in EVENT_TYPES or not isinstance(data, dict) or not event_id or not external_ref:
        raise HTTPException(status_code=422, detail=f"type must be one of {sorted(EVENT_TYPES)}")

    seen = db.scalar(
        select(FieldIntegrationEvent).where(
            FieldIntegrationEvent.integration_id == integration.id, FieldIntegrationEvent.event_id == event_id
        )
    )
    if seen:
        FIELD_WEBHOOKS.labels("inbound", "duplicate").inc()
        return {"duplicate": True, "case_id": seen.case_id, "outcome": seen.outcome}

    source = f"{integration.kind}:{integration.id}"
    case = db.scalar(
        select(ServiceCase).where(
            ServiceCase.workspace_id == integration.workspace_id,
            ServiceCase.external_source == source,
            ServiceCase.external_ref == external_ref,
        )
    )
    actor = integration.created_by
    outcome = "ignored"
    address_changed = False
    pushed = 0
    source_updated_at = _parse_time(payload.get("updated_at") or data.get("updated_at"))
    applied_at = _as_utc(case.external_updated_at) if case is not None else None
    if source_updated_at is not None and applied_at is not None and source_updated_at < applied_at:
        # F5-A: sự kiện đến trễ (CRM gửi lại, hàng đợi đảo thứ tự) — bản đang có mới hơn, bỏ qua.
        logger.info(
            "integration %s: stale event %s for %s ignored (%s < %s)",
            integration.id,
            event_id,
            external_ref,
            source_updated_at.isoformat(),
            applied_at.isoformat(),
        )
        outcome = "stale_ignored"
    elif case is None and event_type in ("ticket.created", "alarm.raised", "ticket.updated"):
        title = str(data.get("title") or "").strip()
        symptom = str(data.get("description") or "").strip()
        if len(title) < 3 or len(symptom) < 3:
            raise HTTPException(status_code=422, detail="data.title and data.description are required to open a case")
        case = ServiceCase(
            workspace_id=integration.workspace_id,
            created_by=actor,
            assigned_to=integration.default_assignee,
            title=title[:255],
            symptom=symptom[:5000],
            external_source=source,
            external_ref=external_ref,
        )
        address_changed = "address" in _apply_fields(case, data)
        db.add(case)
        try:
            db.flush()
        except IntegrityError:
            # Hai sự kiện khác event_id cho cùng external_ref mới đến song song và bản kia đã
            # tạo ca trước (ix_field_case_external). Sự kiện này chưa được ghi nhận nên an toàn
            # khi CRM gửi lại: lần sau sẽ đi nhánh cập nhật ca. Trả 503 có thể thử lại, không 500.
            db.rollback()
            raise HTTPException(
                status_code=503, detail="Concurrent event for this external_ref; retry", headers={"Retry-After": "1"}
            ) from None
        if case.assigned_to is not None:
            # M15: ca CRM tự giao cho người mặc định -> báo người đó (không có người giao nên actor=None).
            from field import push

            pushed += push.enqueue(db, case.assigned_to, case.id, "assigned", actor_id=None)
        label = "ticket CRM" if integration.kind == "crm" else "cảnh báo NMS"
        db.add(
            ServiceCaseEvent(
                case_id=case.id,
                actor_id=actor,
                event_type="created",
                detail=f"Tạo từ {label} {external_ref} ({integration.name})",
            )
        )
        outcome = "created"
    elif case is not None and event_type in ("ticket.created", "alarm.raised", "ticket.updated"):
        changed = _apply_fields(case, data)
        if "address" in changed:
            address_changed = True
            case.location_suggestion_json = None  # gợi ý vị trí (M13) thuộc địa chỉ cũ
        if changed:
            db.add(
                ServiceCaseEvent(
                    case_id=case.id,
                    actor_id=actor,
                    event_type="external",
                    detail=f"{integration.name} cập nhật: {', '.join(changed)}",
                )
            )
        outcome = "updated" if changed else "unchanged"
    elif case is not None and event_type == "ticket.closed":
        if case.status != "resolved":
            case.status = "resolved"
            db.add(
                ServiceCaseEvent(
                    case_id=case.id,
                    actor_id=actor,
                    event_type="status",
                    detail=f"Đóng theo ticket {external_ref} trên {integration.name}",
                )
            )
        outcome = "closed"
    elif case is not None and event_type == "alarm.cleared":
        note = f"Cảnh báo {external_ref} đã hết trên {integration.name}"
        if case.status == "open":
            case.status = "resolved"
            note += " — ca chưa ai xử lý nên được đóng tự động"
        db.add(ServiceCaseEvent(case_id=case.id, actor_id=actor, event_type="external", detail=note))
        outcome = "cleared"
    if (
        case is not None
        and outcome != "stale_ignored"
        and source_updated_at is not None
        and (applied_at is None or source_updated_at > applied_at)
    ):
        case.external_updated_at = source_updated_at
    db.add(
        FieldIntegrationEvent(
            integration_id=integration.id,
            event_id=event_id,
            event_type=event_type,
            case_id=case.id if case else None,
            outcome=outcome,
        )
    )
    try:
        db.commit()
    except IntegrityError:  # cùng event_id đến song song: bản kia đã thắng
        db.rollback()
        FIELD_WEBHOOKS.labels("inbound", "duplicate").inc()
        return {"duplicate": True}
    FIELD_WEBHOOKS.labels("inbound", "stale_ignored" if outcome == "stale_ignored" else "accepted").inc()
    if pushed:
        from field import push

        push.kick()
    if case is not None and address_changed and case.latitude is None and case.address:
        # M13 Q2: CRM không gửi toạ độ → tra nền thành gợi ý chờ trưởng đội xác nhận.
        from field.geocode import schedule_case

        schedule_case(case.id)
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
    payload = {
        "event_id": uuid.uuid4().hex,
        "type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "case": {
            "id": case.id,
            "external_ref": case.external_ref,
            "status": case.status,
            "assigned_to": case.assigned_to,
            "title": case.title,
            "scheduled_start": case.scheduled_start.isoformat() if case.scheduled_start else None,
        },
        **(extra or {}),
    }
    db.add(
        FieldWebhookDelivery(
            integration_id=integration.id,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
            next_attempt_at=datetime.now(UTC),
        )
    )
    return 1


def _backoff(attempts: int) -> timedelta:
    """Chờ trước lần thử thứ attempts+1: 30 s, 60 s, 120 s… (±20% jitter để các delivery hỏng
    cùng lúc không dội lại đối tác cùng một giây). Trần 6 giờ cho MỘT lần chờ không bao giờ chạm
    tới với MAX_ATTEMPTS=8: lần chờ dài nhất là 32 phút, tổng 7 lần chờ ~63,5 phút."""
    import random

    base = min(30 * 2 ** max(attempts - 1, 0), 6 * 3600)
    return timedelta(seconds=base * random.uniform(0.8, 1.2))


def _permanent_failure(status_code: int | None) -> bool:
    """4xx là lỗi của chính yêu cầu (URL sai, bị từ chối): gửi lại y hệt 8 lần không thay đổi gì.
    408 (timeout) và 429 (quá tải) là tạm thời nên vẫn thử lại."""
    return status_code is not None and 400 <= status_code < 500 and status_code not in (408, 429)


class _DeadlineClient:
    """Client mặc định: mỗi POST chạy trong asyncio với hạn chót tổng và đọc giới hạn phản hồi.

    Huỷ (cancel) trong asyncio cắt được cả lúc đang chờ header lẫn body, điều mà timeout theo
    từng lần đọc của client đồng bộ không làm được.
    """

    def post(self, url, content, headers, extensions=None):
        import asyncio

        return asyncio.run(
            asyncio.wait_for(self._post(url, content, headers, extensions or {}), WEBHOOK_DEADLINE_SECONDS)
        )

    @staticmethod
    async def _post(url, content, headers, extensions):
        from types import SimpleNamespace

        import httpx

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            async with client.stream("POST", url, content=content, headers=headers, extensions=extensions) as response:
                received = 0
                async for chunk in response.aiter_raw():
                    received += len(chunk)
                    if received >= MAX_RESPONSE_BYTES:
                        break
                return SimpleNamespace(status_code=response.status_code)

    def close(self) -> None:
        """Mỗi POST tự mở/đóng AsyncClient của nó; không có gì để đóng."""


def dispatch_due(db: Session, *, now: datetime | None = None, client=None, limit: int = 50) -> dict:
    from metrics import FIELD_WEBHOOKS

    now = now or datetime.now(UTC)
    rows = db.scalars(
        select(FieldWebhookDelivery)
        .where(FieldWebhookDelivery.status == "pending", FieldWebhookDelivery.next_attempt_at <= now)
        .order_by(FieldWebhookDelivery.id)
        .limit(limit)
    ).all()
    summary = defaultdict(int)
    own_client = client is None
    client = client or _DeadlineClient()
    try:
        for delivery in rows:
            # Container pipeline và kick() trong API (hoặc nhiều worker) cùng quét hàng đợi.
            # Giành quyền gửi bằng một UPDATE có điều kiện: chỉ một bên thấy rowcount == 1.
            # Nếu tiến trình chết giữa chừng, delivery tự đến hạn lại sau CLAIM_LEASE.
            claimed = db.execute(
                update(FieldWebhookDelivery)
                .where(
                    FieldWebhookDelivery.id == delivery.id,
                    FieldWebhookDelivery.status == "pending",
                    FieldWebhookDelivery.next_attempt_at <= now,
                )
                .values(next_attempt_at=now + CLAIM_LEASE)
                .execution_options(synchronize_session=False)
            ).rowcount
            db.commit()
            if claimed != 1:
                summary["skipped"] += 1
                continue
            db.refresh(delivery)
            integration = db.get(FieldIntegration, delivery.integration_id)
            if not integration or not integration.is_active or not integration.outbound_url:
                delivery.status, delivery.last_error = "dead", "kết nối đã tắt hoặc không còn URL"
                summary["dead"] += 1
                FIELD_WEBHOOKS.labels("outbound", "dead").inc()
                continue
            body = delivery.payload_json.encode()
            timestamp = str(int(time.time()))
            delivery.attempts += 1
            try:
                ips = validate_outbound_url(integration.outbound_url)
                response = _post_pinned(
                    client,
                    integration.outbound_url,
                    ips,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-FieldOps-Timestamp": timestamp,
                        "X-FieldOps-Signature": sign(integration.secret, timestamp, body),
                        "X-FieldOps-Event": delivery.event_type,
                    },
                )
                delivery.response_code = response.status_code
                ok = 200 <= response.status_code < 300
                error = None if ok else f"HTTP {response.status_code}"
            except Exception as exc:  # mạng / DNS / SSRF bị chặn
                ok, error = False, f"{type(exc).__name__}: {str(exc)[:200]}"
            if ok:
                delivery.status, delivery.delivered_at, delivery.last_error = "delivered", now, None
                summary["delivered"] += 1
                FIELD_WEBHOOKS.labels("outbound", "delivered").inc()
            elif delivery.attempts >= MAX_ATTEMPTS or _permanent_failure(
                delivery.response_code if error and error.startswith("HTTP ") else None
            ):
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
    now = now or datetime.now(UTC)
    events = db.execute(
        delete(FieldIntegrationEvent).where(FieldIntegrationEvent.received_at < now - timedelta(days=90))
    ).rowcount
    delivered = db.execute(
        delete(FieldWebhookDelivery).where(
            FieldWebhookDelivery.status == "delivered", FieldWebhookDelivery.created_at < now - timedelta(days=30)
        )
    ).rowcount
    dead = db.execute(
        delete(FieldWebhookDelivery).where(
            FieldWebhookDelivery.status == "dead", FieldWebhookDelivery.created_at < now - timedelta(days=90)
        )
    ).rowcount
    db.commit()
    return {"integration_events_deleted": events or 0, "webhook_deliveries_deleted": (delivered or 0) + (dead or 0)}
