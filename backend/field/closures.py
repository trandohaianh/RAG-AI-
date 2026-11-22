"""M12 — biên bản đóng ca (docs/design/M12_bien_ban_dong_ca.md).

Đóng ca từ giao diện chỉ đi qua đây: mã kết quả + ghi chú + chữ ký khách (trừ khi khách vắng hoặc
từ chối — khi đó cần ít nhất 1 ảnh hiện trường). Chữ ký gửi lên dưới dạng nét (gọn, đi được qua
hàng đợi offline); máy chủ tự vẽ thành ảnh và lưu như ảnh hiện trường.
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from field.models import FieldCaseClosure, ServiceCasePhoto
from field.schemas import CLOSURE_OUTCOMES, NO_SIGNATURE_OUTCOMES, CaseCloseRequest, CaseOut

router = APIRouter(prefix="/field", tags=["field closures"])

SIGNATURE_SIZE = (900, 300)  # khung ký 3:1, khớp với khung ký trên web và mobile
MAX_SIGNATURE_POINTS = 4000
MIN_SIGNATURE_LENGTH_PX = 40
SIGNATURE_CAPTION = "Chữ ký khách hàng (biên bản đóng ca)"


def signature_strokes(strokes: list[list[tuple[float, float]]] | None) -> list[list[tuple[float, float]]]:
    """Giữ các nét có ít nhất 2 điểm, kẹp toạ độ về 0..1. Trả rỗng = coi như chưa ký."""
    if not strokes:
        return []
    if sum(len(stroke) for stroke in strokes) > MAX_SIGNATURE_POINTS:
        raise HTTPException(status_code=422, detail="Chữ ký quá phức tạp, vui lòng ký lại")
    cleaned = []
    for stroke in strokes:
        points = [(min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)) for x, y in stroke]
        if len(points) >= 2:
            cleaned.append(points)
    # Một chấm hay vệt quá ngắn không phải chữ ký.
    length = sum(
        abs(a[0] - b[0]) * SIGNATURE_SIZE[0] + abs(a[1] - b[1]) * SIGNATURE_SIZE[1]
        for stroke in cleaned
        for a, b in zip(stroke, stroke[1:], strict=False)
    )
    return cleaned if length >= MIN_SIGNATURE_LENGTH_PX else []


def render_signature(strokes: list[list[tuple[float, float]]]) -> tuple[bytes, bytes, int, int]:
    """Vẽ nét ký thành ảnh JPEG nền trắng (ảnh đầy đủ + ảnh nhỏ)."""
    from PIL import Image, ImageDraw

    width, height = SIGNATURE_SIZE
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for stroke in strokes:
        draw.line([(x * width, y * height) for x, y in stroke], fill=(17, 24, 39), width=5, joint="curve")
    full = io.BytesIO()
    image.save(full, "JPEG", quality=90)
    thumb_image = image.copy()
    thumb_image.thumbnail((360, 360))
    thumb = io.BytesIO()
    thumb_image.save(thumb, "JPEG", quality=80)
    return full.getvalue(), thumb.getvalue(), width, height


@router.post("/cases/{case_id}/close", response_model=CaseOut)
def close_case(
    case_id: int,
    body: CaseCloseRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from field.integrations import enqueue_case_event, kick
    from field.photos import store_generated_image
    from field.router import _add_event, _case_for_user, _claim_client_action, _member_role

    case = _case_for_user(db, case_id, user.id)
    # Q3: kỹ thuật viên được giao và trưởng đội.
    if _member_role(db, case.workspace_id, user.id) != "owner" and case.assigned_to != user.id:
        raise HTTPException(status_code=403, detail="Chỉ kỹ thuật viên được giao ca hoặc trưởng đội được đóng ca")
    _, already_applied = _claim_client_action(
        db,
        client_action_id=body.client_action_id,
        case_id=case.id,
        actor_id=user.id,
        action_type="case_close",
        request_body=body,
    )
    if already_applied:
        return _case_for_user(db, case.id, user.id)
    if case.status == "resolved":
        raise HTTPException(status_code=409, detail="Ca đã được đóng trước đó")
    note = body.note.strip()
    if len(note) < 3:
        raise HTTPException(status_code=422, detail="Cần ghi chú kết quả (ít nhất 3 ký tự)")
    strokes = signature_strokes(body.signature)
    if body.outcome_code in NO_SIGNATURE_OUTCOMES:
        # Q2: không có khách để ký → bằng chứng là ảnh hiện trường.
        photos = db.scalar(
            select(func.count())
            .select_from(ServiceCasePhoto)
            .where(ServiceCasePhoto.case_id == case.id, ServiceCasePhoto.caption != SIGNATURE_CAPTION)
        )
        if not photos:
            raise HTTPException(
                status_code=422,
                detail="Khách vắng hoặc từ chối: cần chụp ít nhất 1 ảnh hiện trường làm bằng chứng",
            )
    elif not strokes:
        raise HTTPException(status_code=422, detail="Cần chữ ký của khách hàng để đóng ca")

    written: list[Path] = []
    signature_photo = None
    if strokes:
        full, thumb, width, height = render_signature(strokes)
        signature_photo, written = store_generated_image(
            db, case, user.id, full, thumb, width, height, SIGNATURE_CAPTION
        )
        db.flush()
    customer = (body.customer_name or "").strip() or None
    db.add(
        FieldCaseClosure(
            case_id=case.id,
            outcome_code=body.outcome_code,
            note=note,
            customer_name=customer,
            signature_photo_id=signature_photo.id if signature_photo else None,
            signed=signature_photo is not None,
            closed_by=user.id,
            client_action_id=body.client_action_id,
        )
    )
    case.status = "resolved"
    label = CLOSURE_OUTCOMES[body.outcome_code]
    if signature_photo:
        signed_text = "có chữ ký khách" + (f" ({customer})" if customer else "")
    else:
        signed_text = "không có chữ ký"
    _add_event(db, case.id, user.id, "closed", f"Đóng ca — Kết quả: {label} — {signed_text}. Ghi chú: {note[:300]}")
    # Q4: báo CRM kết quả, KHÔNG gửi ảnh chữ ký (dữ liệu cá nhân không rời FieldOps).
    queued = enqueue_case_event(
        db,
        case,
        "case.status_changed",
        {
            "closure": {
                "outcome_code": body.outcome_code,
                "outcome_label": label,
                "note": note,
                "signed": signature_photo is not None,
                "customer_name": customer,
            }
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise
    if queued:
        kick()
    return _case_for_user(db, case.id, user.id)
