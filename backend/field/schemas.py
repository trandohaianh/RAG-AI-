"""Hằng số trạng thái và schema (Pydantic) của API ca hiện trường.

Tách khỏi field/router.py để router chỉ còn luồng xử lý request (quy ước: không để một file
gánh mọi thứ, docs/CONVENTIONS.md).
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer, computed_field

from rag.evidence import CitationAudit, EvidenceOut


def _utc_iso(value: datetime) -> str:
    # SQLite trả datetime KHÔNG có múi giờ (giá trị là giờ UTC). Gửi ra không kèm múi giờ thì app/web
    # hiểu nhầm thành giờ địa phương: lịch 07:04 (VN) hiện thành 00:04. Luôn kèm +00:00.
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str)]

CASE_STATUSES = {"open", "in_progress", "waiting", "escalated", "resolved"}
STEP_STATUSES = {"todo", "done", "blocked"}
# Nhãn tiếng Việt cho nhật ký ca (mã trạng thái vẫn lưu ở event_type / trường status).
CASE_STATUS_VI = {
    "open": "Mới tiếp nhận",
    "in_progress": "Đang xử lý",
    "waiting": "Chờ khách hàng",
    "escalated": "Chuyển cấp",
    "resolved": "Đã hoàn tất",
}
STEP_STATUS_VI = {"todo": "chưa làm", "done": "đã xong", "blocked": "bị vướng"}

# M14: thông tin ca trưởng đội được sửa sau khi tạo, kèm nhãn tiếng Việt cho nhật ký.
CASE_DETAIL_FIELDS = {
    "title": "Tiêu đề",
    "symptom": "Triệu chứng/bàn giao",
    "customer_label": "Khách hàng",
    "address": "Địa chỉ",
    "device_model": "Model",
    "serial_number": "Serial",
}


# M12 — 6 mã kết quả cố định (chủ dự án chọn 2026-11-22).
CLOSURE_OUTCOMES = {
    "fixed": "Đã sửa xong",
    "replaced": "Thay thiết bị",
    "not_reproduced": "Không tái hiện lỗi",
    "escalated": "Chuyển cấp kỹ thuật",
    "customer_absent": "Khách vắng nhà",
    "customer_refused": "Khách từ chối sửa",
}
# Không có khách để ký: thay chữ ký bằng ghi chú + ít nhất 1 ảnh hiện trường (Q2).
NO_SIGNATURE_OUTCOMES = {"customer_absent", "customer_refused"}
OutcomeCode = Literal["fixed", "replaced", "not_reproduced", "escalated", "customer_absent", "customer_refused"]


def details_version(values: dict) -> str:
    """Dấu phiên bản của thông tin ca (6 trường ở CASE_DETAIL_FIELDS).

    Dùng phát hiện hai người sửa cùng lúc (M14, Q3) mà không cần thêm cột: `updated_at` trên
    SQLite chỉ chính xác tới giây và đổi cả khi chỉ trạng thái ca đổi.
    """
    payload = json.dumps({key: values.get(key) for key in CASE_DETAIL_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class WorkspaceOut(BaseModel):
    id: int
    name: str
    role: str
    member_count: int


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class MemberOut(BaseModel):
    user_id: int
    email: str
    role: str
    base_latitude: float | None = None
    base_longitude: float | None = None
    shift_start: str = "08:00"
    shift_end: str = "17:00"


class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    symptom: str = Field(min_length=3, max_length=5000)
    customer_label: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=500)
    device_model: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    assigned_to: int | None = None


class CaseUpdate(BaseModel):
    status: str | None = None
    assigned_to: int | None = None
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class CaseDetailsUpdate(BaseModel):
    """M14 — trưởng đội sửa thông tin ca. Chỉ trường được gửi mới bị đổi."""

    title: str | None = Field(default=None, min_length=3, max_length=255)
    symptom: str | None = Field(default=None, min_length=3, max_length=5000)
    customer_label: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=500)
    device_model: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    # details_version mà màn hình đang thấy (CaseOut.details_version); khác bản hiện tại → 409.
    expected_version: str = Field(min_length=8, max_length=64)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class CaseCloseRequest(BaseModel):
    """M12 — biên bản đóng ca. `signature`: các nét ký, mỗi nét là danh sách điểm [x, y] đã chuẩn
    hoá về 0..1 theo khung ký 3:1; máy chủ tự vẽ thành ảnh."""

    outcome_code: OutcomeCode
    note: str = Field(min_length=3, max_length=2000)
    customer_name: str | None = Field(default=None, max_length=120)
    signature: list[list[tuple[float, float]]] | None = Field(default=None, max_length=60)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class ClosureOut(BaseModel):
    id: int
    outcome_code: str
    note: str
    customer_name: str | None
    signature_photo_id: int | None
    signed: bool
    closed_by: int
    closed_at: UtcDatetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def outcome_label(self) -> str:
        return CLOSURE_OUTCOMES.get(self.outcome_code, self.outcome_code)

    @computed_field
    @property
    def signature_url(self) -> str | None:
        if self.signature_photo_id is None:
            return None
        from field.photos import signed_url

        return signed_url(self.signature_photo_id, "full")


class StepCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    instruction: str = Field(default="", max_length=5000)
    source: str | None = Field(default=None, max_length=255)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class StepUpdate(BaseModel):
    status: str
    result_note: str | None = Field(default=None, max_length=3000)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class EventCreate(BaseModel):
    detail: str = Field(min_length=1, max_length=5000)
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class AssistRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class EventOut(BaseModel):
    evidence: list[EvidenceOut] = Field(default_factory=list)
    citation_audit: CitationAudit | None = None
    id: int
    actor_id: int
    event_type: str
    detail: str
    created_at: UtcDatetime

    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: int
    title: str
    instruction: str
    source: str | None
    status: str
    result_note: str | None
    position: int
    completed_by: int | None
    completed_at: UtcDatetime | None

    model_config = {"from_attributes": True}


class CaseOut(BaseModel):
    id: int
    workspace_id: int
    created_by: int
    assigned_to: int | None
    title: str
    customer_label: str | None
    address: str | None
    device_model: str | None
    serial_number: str | None
    symptom: str
    status: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    latitude: float | None = None
    longitude: float | None = None
    priority: str = "normal"
    window_start: UtcDatetime | None = None
    window_end: UtcDatetime | None = None
    est_minutes: int = 60
    scheduled_start: UtcDatetime | None = None
    route_order: int | None = None
    external_source: str | None = None
    external_ref: str | None = None
    steps: list[StepOut] = []
    events: list[EventOut] = []
    closure: ClosureOut | None = None
    # M13: vị trí đề xuất chờ trưởng đội xác nhận (đọc từ cột JSON, trả ra dạng object).
    location_suggestion_json: str | None = Field(default=None, exclude=True)

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def location_suggestion(self) -> dict | None:
        if not self.location_suggestion_json:
            return None
        try:
            value = json.loads(self.location_suggestion_json)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    @computed_field
    @property
    def details_version(self) -> str:
        return details_version({key: getattr(self, key) for key in CASE_DETAIL_FIELDS})
