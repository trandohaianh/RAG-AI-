"""M13 — địa chỉ → toạ độ (docs/design/M13_dia_chi_toa_do.md).

Tra địa chỉ qua Nominatim (OpenStreetMap, miễn phí) TỪ MÁY CHỦ để:
- giữ đúng chính sách Nominatim: ≤ 1 yêu cầu/giây cho cả hệ thống, User-Agent có liên hệ,
  có cache, không gợi ý theo từng phím gõ (chỉ tra khi người dùng bấm nút);
- máy chủ là nơi duy nhất quyết định gửi gì ra ngoài: CHỈ chuỗi địa chỉ, không tên khách, mã ca.

Kết quả luôn là GỢI Ý: toạ độ chỉ được dùng xếp tuyến sau khi trưởng đội xác nhận trên bản đồ.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from field.models import FieldGeocodeCache, FieldWorkspaceMember, ServiceCase, ServiceCaseEvent
from field.schemas import CaseOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/field", tags=["field geocode"])

MIN_INTERVAL_SECONDS = 1.1  # chính sách Nominatim: tối đa 1 yêu cầu/giây
WAIT_LIMIT_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 8.0
NOT_FOUND_CACHE = timedelta(days=1)
MAX_RESULTS = 3

PRECISION_LABEL = {
    "house": "Chính xác đến số nhà",
    "street": "Chỉ chính xác đến tên đường",
    "ward": "Chỉ chính xác đến phường/xã",
    "area": "Chỉ chính xác đến quận/tỉnh",
}
_STREET_TYPES = {"road", "street", "residential", "primary", "secondary", "tertiary", "trunk", "unclassified"}
_WARD_TYPES = {"quarter", "suburb", "neighbourhood", "village", "hamlet", "town", "isolated_dwelling", "locality"}

# Có thể thay trong test (đồng hồ giả, transport giả) — test KHÔNG bao giờ gọi Nominatim thật.
_monotonic = time.monotonic
_sleep = time.sleep
_transport: httpx.BaseTransport | None = None
_gate = threading.Lock()
_last_call = [float("-inf")]


class GeocoderUnavailable(Exception):
    """Dịch vụ ngoài lỗi / chậm / từ chối: không cache, người dùng nhập tay hoặc thử lại."""


class GeocoderBusy(Exception):
    """Chờ quá lâu đến lượt gọi (nhiều người tra cùng lúc)."""


def enabled() -> bool:
    return bool(config.NOMINATIM_CONTACT)


def normalize_address(address: str) -> str:
    text = re.sub(r"\s+", " ", address).strip().strip(",.;").strip()
    return text


def _query_hash(address: str) -> str:
    return hashlib.sha256(normalize_address(address).casefold().encode("utf-8")).hexdigest()


def _precision(item: dict) -> str:
    details = item.get("address") or {}
    kind = str(item.get("addresstype") or item.get("type") or "")
    if details.get("house_number") or kind in {"house", "building"}:
        return "house"
    if kind in _STREET_TYPES or item.get("category") == "highway":
        return "street"
    if kind in _WARD_TYPES:
        return "ward"
    return "area"


def _results(payload) -> list[dict]:
    out = []
    for item in payload if isinstance(payload, list) else []:
        try:
            lat, lng = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        precision = _precision(item)
        out.append(
            {
                "latitude": round(lat, 7),
                "longitude": round(lng, 7),
                "label": str(item.get("display_name") or "")[:300],
                "precision": precision,
                "precision_label": PRECISION_LABEL[precision],
            }
        )
        if len(out) >= MAX_RESULTS:
            break
    return out


def _wait_turn() -> None:
    """Giữ nhịp ≤ 1 yêu cầu/giây cho cả tiến trình (deploy chạy --workers 1)."""
    if not _gate.acquire(timeout=WAIT_LIMIT_SECONDS):
        raise GeocoderBusy
    try:
        delay = _last_call[0] + MIN_INTERVAL_SECONDS - _monotonic()
        if delay > 0:
            _sleep(delay)
        _last_call[0] = _monotonic()
    finally:
        _gate.release()


def _fetch(address: str) -> list[dict]:
    _wait_turn()
    headers = {"User-Agent": f"FieldOps-geocoder/1.0 (+{config.NOMINATIM_CONTACT})"}
    params = {
        "q": normalize_address(address),
        "countrycodes": "vn",
        "format": "jsonv2",
        "addressdetails": "1",
        "limit": str(MAX_RESULTS),
        "accept-language": "vi",
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, transport=_transport, headers=headers) as client:
            response = client.get(config.NOMINATIM_URL, params=params)
    except httpx.HTTPError as exc:
        raise GeocoderUnavailable(str(exc)) from exc
    if response.status_code != 200:
        raise GeocoderUnavailable(f"HTTP {response.status_code}")
    try:
        return _results(response.json())
    except ValueError as exc:
        raise GeocoderUnavailable("bad JSON") from exc


def lookup(db: Session, workspace_id: int, address: str, now: datetime | None = None) -> tuple[list[dict], bool]:
    """Trả (gợi ý, lấy từ cache?). Cache riêng từng đội (Q3); "không thấy" chỉ nhớ 1 ngày."""
    now = now or datetime.now(UTC)
    digest = _query_hash(address)
    row = db.scalar(
        select(FieldGeocodeCache).where(
            FieldGeocodeCache.workspace_id == workspace_id, FieldGeocodeCache.query_hash == digest
        )
    )
    if row is not None:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
        cached = json.loads(row.results_json)
        ttl = timedelta(days=config.GEOCODE_CACHE_DAYS) if cached else NOT_FOUND_CACHE
        if now - created < ttl:
            return cached, True
    results = _fetch(address)
    if row is None:
        row = FieldGeocodeCache(workspace_id=workspace_id, query_hash=digest, results_json="[]")
        db.add(row)
    row.results_json = json.dumps(results, ensure_ascii=False)
    row.created_at = now
    try:
        db.commit()
    except IntegrityError:  # người khác vừa tra cùng địa chỉ: kết quả của họ cũng đúng
        db.rollback()
    return results, False


def purge_expired(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    deleted = db.execute(
        delete(FieldGeocodeCache).where(FieldGeocodeCache.created_at < now - timedelta(days=config.GEOCODE_CACHE_DAYS))
    ).rowcount
    db.commit()
    return int(deleted or 0)


# ----------------------------------------------------------------------------- vị trí đề xuất
def set_suggestion(
    case: ServiceCase,
    *,
    latitude: float,
    longitude: float,
    source: str,
    by: int | None,
    label: str | None = None,
    precision: str | None = None,
) -> None:
    case.location_suggestion_json = json.dumps(
        {
            "latitude": latitude,
            "longitude": longitude,
            "source": source,
            "label": label,
            "precision": precision,
            "precision_label": PRECISION_LABEL.get(precision or ""),
            "suggested_by": by,
            "suggested_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
    )


def suggest_for_case(db: Session, case_id: int) -> str:
    """Q2: tự tra địa chỉ của ca (từ CRM) thành gợi ý chờ xác nhận. Trả kết quả để ghi log/test."""
    case = db.get(ServiceCase, case_id)
    if case is None or case.latitude is not None or not case.address:
        return "skipped"
    address = case.address
    try:
        results, _ = lookup(db, case.workspace_id, address)
    except (GeocoderUnavailable, GeocoderBusy):
        logger.warning("geocode for case %s failed; owner can retry manually", case_id)
        return "unavailable"
    db.refresh(case)
    if case.address != address or case.latitude is not None:
        return "stale"  # địa chỉ vừa đổi hoặc đã có toạ độ trong lúc tra
    if results:
        best = results[0]
        set_suggestion(
            case,
            latitude=best["latitude"],
            longitude=best["longitude"],
            source="geocoded",
            by=None,
            label=best["label"],
            precision=best["precision"],
        )
        detail = f"Tự tra toạ độ từ địa chỉ — chờ trưởng đội xác nhận ({best['precision_label'].lower()})"
    else:
        detail = "Không tìm thấy toạ độ cho địa chỉ trên OpenStreetMap — cần đặt vị trí bằng tay"
    db.add(ServiceCaseEvent(case_id=case.id, actor_id=case.created_by, event_type="location", detail=detail))
    db.commit()
    return "suggested" if results else "not_found"


_pending: set[int] = set()
_pending_lock = threading.Lock()


def schedule_case(case_id: int) -> None:
    """Tra nền sau khi ca CRM vào (không giữ request của CRM chờ Nominatim)."""
    if not enabled():
        return
    with _pending_lock:
        if case_id in _pending:
            return
        _pending.add(case_id)

    def run():
        try:
            from database import SessionLocal

            with SessionLocal() as db:
                suggest_for_case(db, case_id)
        except Exception:
            logger.exception("background geocode failed for case %s", case_id)
        finally:
            with _pending_lock:
                _pending.discard(case_id)

    threading.Thread(target=run, name="geocode-case", daemon=True).start()


# ----------------------------------------------------------------------------- API
class GeocodeRequest(BaseModel):
    address: str = Field(min_length=5, max_length=300)


class SuggestionRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    source: Literal["gps", "geocoded", "manual"]
    client_action_id: str | None = Field(default=None, min_length=8, max_length=64)


def _member(db: Session, workspace_id: int, user_id: int) -> FieldWorkspaceMember:
    member = db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy đội")
    return member


@router.post("/workspaces/{workspace_id}/geocode")
def geocode(
    workspace_id: int,
    body: GeocodeRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _member(db, workspace_id, user.id)
    if not enabled():
        raise HTTPException(
            status_code=503, detail="Chưa bật tra toạ độ (thiếu NOMINATIM_CONTACT) — hãy nhập toạ độ bằng tay"
        )
    if len(normalize_address(body.address)) < 5:
        raise HTTPException(status_code=422, detail="Địa chỉ quá ngắn để tra")
    try:
        results, cached = lookup(db, workspace_id, body.address)
    except GeocoderBusy:
        raise HTTPException(status_code=503, detail="Dịch vụ tra toạ độ đang bận, thử lại sau ít giây") from None
    except GeocoderUnavailable:
        raise HTTPException(
            status_code=502, detail="Không tra được toạ độ lúc này — hãy đặt vị trí bằng tay hoặc thử lại sau"
        ) from None
    return {
        "results": results,
        "cached": cached,
        "message": None if results else "Không tìm thấy địa chỉ này trên OpenStreetMap — hãy đặt vị trí bằng tay",
        "attribution": "© OpenStreetMap contributors",
    }


@router.post("/cases/{case_id}/location-suggestion", response_model=CaseOut)
def suggest_location(
    case_id: int,
    body: SuggestionRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Q5: kỹ thuật viên được giao gửi vị trí đề xuất; trưởng đội xác nhận rồi mới dùng xếp tuyến."""
    from field.router import _add_event, _case_for_user, _claim_client_action, _member_role

    case = _case_for_user(db, case_id, user.id)
    if _member_role(db, case.workspace_id, user.id) != "owner" and case.assigned_to != user.id:
        raise HTTPException(status_code=403, detail="Chỉ kỹ thuật viên được giao ca hoặc trưởng đội được gửi vị trí")
    _, already_applied = _claim_client_action(
        db,
        client_action_id=body.client_action_id,
        case_id=case.id,
        actor_id=user.id,
        action_type="location_suggest",
        request_body=body,
    )
    if already_applied:
        return _case_for_user(db, case.id, user.id)
    if case.status == "resolved":
        raise HTTPException(status_code=409, detail="Ca đã đóng")
    set_suggestion(case, latitude=body.latitude, longitude=body.longitude, source=body.source, by=user.id)
    how = {"gps": "GPS tại nhà khách", "geocoded": "tìm từ địa chỉ", "manual": "chọn trên bản đồ"}[body.source]
    _add_event(db, case.id, user.id, "location", f"Gửi vị trí đề xuất ({how}) — chờ trưởng đội xác nhận")
    db.commit()
    return _case_for_user(db, case.id, user.id)
