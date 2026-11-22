"""Ảnh hiện trường gắn với ca sửa.

- Ảnh được MÃ HOÁ LẠI bằng Pillow: xoay đúng chiều theo EXIF rồi bỏ toàn bộ metadata (GPS,
  model máy, giờ chụp chi tiết) — ảnh của nhà khách hàng không được mang toạ độ ra ngoài.
- Chống "decompression bomb" (giới hạn số điểm ảnh), chỉ nhận JPEG/PNG/WebP thật (đọc nội
  dung, không tin phần mở rộng hay Content-Type), giới hạn dung lượng tệp, số ảnh mỗi ca và
  tổng dung lượng mỗi đội.
- Tệp nằm ngoài thư mục web; trình duyệt tải qua URL ký HMAC có hạn 15 phút (thẻ <img> không
  gửi được header Authorization).
- Hạn lưu giữ: ảnh của ca đã "resolved" quá FIELD_PHOTO_RETENTION_DAYS bị xoá bởi pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import config
from auth.schemas import CurrentUser
from database import get_db
from dependencies import get_current_user
from field.models import (
    FieldCaseClosure,
    FieldWorkspace,
    FieldWorkspaceMember,
    ServiceCase,
    ServiceCaseEvent,
    ServiceCasePhoto,
)

router = APIRouter(prefix="/field", tags=["field photos"])

MAX_PIXELS = 40_000_000
MAX_EDGE = 2560
THUMB_EDGE = 360
URL_TTL_SECONDS = 900
_ALLOWED = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class PhotoOut(BaseModel):
    id: int
    case_id: int
    uploaded_by: int
    caption: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime
    url: str
    thumb_url: str


def _sign(photo_id: int, variant: str, expires: int) -> str:
    message = f"{photo_id}:{variant}:{expires}".encode()
    return hmac.new(config.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()[:40]


def signed_url(photo_id: int, variant: str = "full", now: float | None = None) -> str:
    expires = int((now or time.time()) + URL_TTL_SECONDS)
    return f"{config.API_PUBLIC_URL.rstrip('/')}/field/photos/{photo_id}/file?v={variant}&exp={expires}&sig={_sign(photo_id, variant, expires)}"


def _to_out(photo: ServiceCasePhoto) -> PhotoOut:
    return PhotoOut(
        id=photo.id,
        case_id=photo.case_id,
        uploaded_by=photo.uploaded_by,
        caption=photo.caption,
        width=photo.width,
        height=photo.height,
        size_bytes=photo.size_bytes,
        created_at=photo.created_at,
        url=signed_url(photo.id, "full"),
        thumb_url=signed_url(photo.id, "thumb"),
    )


def _path(storage_key: str, variant: str) -> Path:
    suffix = "_thumb.jpg" if variant == "thumb" else ".jpg"
    return config.FIELD_PHOTO_DIR / f"{storage_key}{suffix}"


def process_image(data: bytes) -> tuple[bytes, bytes, int, int]:
    """Kiểm tra + mã hoá lại -> (jpeg_full, jpeg_thumb, width, height). Raise ValueError nếu không hợp lệ."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in _ALLOWED:
                raise ValueError("Chỉ nhận ảnh JPEG, PNG hoặc WebP")
            # Pillow chỉ raise DecompressionBombError khi vượt 2× MAX_IMAGE_PIXELS (từ 1× đến 2×
            # nó chỉ cảnh báo) nên phải tự chặn trước khi giải nén toàn bộ ảnh vào RAM.
            if probe.width * probe.height > MAX_PIXELS:
                raise ValueError("Ảnh có độ phân giải quá lớn")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            elif image.mode == "L":
                image = image.convert("RGB")
            image.thumbnail((MAX_EDGE, MAX_EDGE))
            full = io.BytesIO()
            image.save(full, "JPEG", quality=85, optimize=True)  # không truyền exif -> metadata bị bỏ
            thumb_image = image.copy()
            thumb_image.thumbnail((THUMB_EDGE, THUMB_EDGE))
            thumb = io.BytesIO()
            thumb_image.save(thumb, "JPEG", quality=78)
            return full.getvalue(), thumb.getvalue(), image.width, image.height
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ValueError("Tệp không phải ảnh hợp lệ hoặc quá lớn") from exc


def _membership(db: Session, workspace_id: int, user_id: int) -> FieldWorkspaceMember | None:
    return db.scalar(
        select(FieldWorkspaceMember).where(
            FieldWorkspaceMember.workspace_id == workspace_id, FieldWorkspaceMember.user_id == user_id
        )
    )


def _case_for_member(db: Session, case_id: int, user_id: int) -> tuple[ServiceCase, FieldWorkspaceMember]:
    case = db.get(ServiceCase, case_id)
    member = _membership(db, case.workspace_id, user_id) if case else None
    if not case or not member:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca sửa")
    return case, member


def workspace_photo_bytes(db: Session, workspace_id: int) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(ServiceCasePhoto.size_bytes), 0))
            .join(ServiceCase, ServiceCase.id == ServiceCasePhoto.case_id)
            .where(ServiceCase.workspace_id == workspace_id)
        )
        or 0
    )


@router.post("/cases/{case_id}/photos", response_model=PhotoOut, status_code=201)
async def upload_photo(
    case_id: int,
    file: UploadFile = File(...),
    caption: str = Form(default="", max_length=300),
    client_action_id: str | None = Form(default=None, min_length=8, max_length=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case, _ = _case_for_member(db, case_id, user.id)
    if client_action_id:  # hàng đợi offline của app gửi lại: trả kết quả cũ, không tạo trùng
        existing = db.scalar(select(ServiceCasePhoto).where(ServiceCasePhoto.client_action_id == client_action_id))
        if existing:
            if existing.case_id != case.id:
                raise HTTPException(status_code=409, detail="Mã thao tác đã được sử dụng")
            return _to_out(existing)
    limit = config.FIELD_PHOTO_MAX_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"Ảnh vượt quá {config.FIELD_PHOTO_MAX_MB} MB")
    count = db.scalar(select(func.count()).select_from(ServiceCasePhoto).where(ServiceCasePhoto.case_id == case.id))
    if count >= config.FIELD_PHOTO_MAX_PER_CASE:
        raise HTTPException(status_code=409, detail=f"Mỗi ca tối đa {config.FIELD_PHOTO_MAX_PER_CASE} ảnh")
    try:
        full, thumb, width, height = await asyncio.get_running_loop().run_in_executor(None, process_image, data)
    except ValueError as exc:
        from metrics import FIELD_PHOTOS

        FIELD_PHOTOS.labels("rejected").inc()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    case_id, workspace_id = case.id, case.workspace_id
    # Kiểm tra lại hạn mức SAU khi giữ khoá ghi của đội: nhiều ảnh upload song song (outbox
    # offline xả hàng đợi) đều qua bước đếm ở trên trước khi bản nào kịp commit. UPDATE không
    # đổi gì lấy khoá ghi trên SQLite và khoá dòng đội trên PostgreSQL cho tới khi commit.
    db.rollback()  # kết thúc transaction chỉ-đọc trước khi xin khoá ghi
    db.execute(
        update(FieldWorkspace)
        .where(FieldWorkspace.id == workspace_id)
        .values(name=FieldWorkspace.name)
        .execution_options(synchronize_session=False)
    )
    count = db.scalar(select(func.count()).select_from(ServiceCasePhoto).where(ServiceCasePhoto.case_id == case_id))
    if count >= config.FIELD_PHOTO_MAX_PER_CASE:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Mỗi ca tối đa {config.FIELD_PHOTO_MAX_PER_CASE} ảnh")
    if workspace_photo_bytes(db, workspace_id) + len(full) > config.FIELD_PHOTO_WORKSPACE_QUOTA_MB * 1024 * 1024:
        db.rollback()
        raise HTTPException(status_code=409, detail="Đội đã dùng hết dung lượng ảnh")
    key = f"{workspace_id}/{case_id}/{uuid.uuid4().hex}"
    target = _path(key, "full")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(full)
    _path(key, "thumb").write_bytes(thumb)
    photo = ServiceCasePhoto(
        case_id=case.id,
        uploaded_by=user.id,
        storage_key=key,
        sha256=hashlib.sha256(full).hexdigest(),
        content_type="image/jpeg",
        size_bytes=len(full),
        width=width,
        height=height,
        caption=caption.strip(),
        client_action_id=client_action_id,
    )
    db.add(photo)
    db.add(
        ServiceCaseEvent(
            case_id=case.id,
            actor_id=user.id,
            event_type="photo",
            detail=f"Thêm ảnh hiện trường{': ' + caption.strip() if caption.strip() else ''}",
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        for variant in ("full", "thumb"):
            _path(key, variant).unlink(missing_ok=True)
        raise
    db.refresh(photo)
    from metrics import FIELD_PHOTOS

    FIELD_PHOTOS.labels("uploaded").inc()
    return _to_out(photo)


def store_generated_image(
    db: Session, case: ServiceCase, user_id: int, full: bytes, thumb: bytes, width: int, height: int, caption: str
) -> tuple[ServiceCasePhoto, list[Path]]:
    """Ghi một ảnh do máy chủ tạo (vd. chữ ký M12) vào cùng kho ảnh hiện trường: cùng thư mục,
    cùng URL ký có hạn, cùng hạn giữ và bản sao lưu. Chưa commit; trả về các tệp đã ghi để xoá
    nếu giao dịch thất bại."""
    key = f"{case.workspace_id}/{case.id}/{uuid.uuid4().hex}"
    paths = [_path(key, "full"), _path(key, "thumb")]
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    paths[0].write_bytes(full)
    paths[1].write_bytes(thumb)
    photo = ServiceCasePhoto(
        case_id=case.id,
        uploaded_by=user_id,
        storage_key=key,
        sha256=hashlib.sha256(full).hexdigest(),
        content_type="image/jpeg",
        size_bytes=len(full),
        width=width,
        height=height,
        caption=caption,
    )
    db.add(photo)
    return photo, paths


def is_signature(db: Session, photo_id: int) -> bool:
    return db.scalar(select(FieldCaseClosure.id).where(FieldCaseClosure.signature_photo_id == photo_id)) is not None


@router.get("/cases/{case_id}/photos", response_model=list[PhotoOut])
def list_photos(case_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    case, _ = _case_for_member(db, case_id, user.id)
    rows = db.scalars(
        select(ServiceCasePhoto)
        .where(ServiceCasePhoto.case_id == case.id)
        .order_by(ServiceCasePhoto.created_at, ServiceCasePhoto.id)
    ).all()
    return [_to_out(photo) for photo in rows]


@router.get("/photos/{photo_id}/file")
def photo_file(
    photo_id: int,
    v: str = Query("full", pattern="^(full|thumb)$"),
    exp: int = Query(...),
    sig: str = Query(..., min_length=40, max_length=40),
    db: Session = Depends(get_db),
):
    if exp < time.time() or not hmac.compare_digest(sig, _sign(photo_id, v, exp)):
        raise HTTPException(status_code=403, detail="Liên kết ảnh đã hết hạn")
    photo = db.get(ServiceCasePhoto, photo_id)
    path = _path(photo.storage_key, v) if photo else None
    if not photo or not path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/photos/{photo_id}", status_code=204)
def delete_photo(photo_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    photo = db.get(ServiceCasePhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh")
    case, member = _case_for_member(db, photo.case_id, user.id)
    if is_signature(db, photo.id):
        # Chữ ký là bằng chứng của biên bản đóng ca (M12): không xoá lẻ được.
        raise HTTPException(status_code=409, detail="Ảnh chữ ký thuộc biên bản đóng ca, không xoá được")
    if photo.uploaded_by != user.id and member.role != "owner":
        raise HTTPException(status_code=403, detail="Chỉ người chụp hoặc trưởng đội được xoá ảnh")
    remove_photo(db, photo)
    db.add(ServiceCaseEvent(case_id=case.id, actor_id=user.id, event_type="photo", detail="Xoá một ảnh hiện trường"))
    db.commit()


def remove_photo(db: Session, photo: ServiceCasePhoto) -> None:
    # Hết hạn giữ thì chữ ký bị xoá như mọi ảnh; biên bản vẫn còn, chỉ mất liên kết ảnh.
    db.execute(
        update(FieldCaseClosure)
        .where(FieldCaseClosure.signature_photo_id == photo.id)
        .values(signature_photo_id=None)
        .execution_options(synchronize_session=False)
    )
    for variant in ("full", "thumb"):
        _path(photo.storage_key, variant).unlink(missing_ok=True)
    db.delete(photo)


def purge_expired_photos(db: Session, now: datetime | None = None) -> int:
    """Xoá ảnh của ca đã resolved quá FIELD_PHOTO_RETENTION_DAYS (dựa trên updated_at của ca)."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=config.FIELD_PHOTO_RETENTION_DAYS)
    rows = db.scalars(
        select(ServiceCasePhoto)
        .join(ServiceCase, ServiceCase.id == ServiceCasePhoto.case_id)
        .where(ServiceCase.status == "resolved", ServiceCase.updated_at < cutoff)
    ).all()
    for photo in rows:
        remove_photo(db, photo)
    db.commit()
    return len(rows)
