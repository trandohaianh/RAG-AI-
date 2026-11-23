"""Bước 7 (P1): quyền của chủ thể dữ liệu (Luật 91/2025/QH15, Nghị định 356/2025/NĐ-CP).

- `POST /auth/me/consent`: đồng ý phiên bản Chính sách quyền riêng tư hiện hành (lưu nhật ký).
- `GET  /auth/me/export`: tải dữ liệu cá nhân của tôi (JSON).
- `GET  /auth/me/delete-preview`: xoá tài khoản sẽ ảnh hưởng gì.
- `POST /auth/me/delete`: xoá tài khoản. Dùng lại `deactivate_account` (ẩn danh email, thu hồi
  phiên, rời mọi đội, bỏ giao ca), cộng thêm: xoá hội thoại trợ lý, máy nhận thông báo, nhật ký
  đồng ý, tài liệu cá nhân, và **xoá hẳn các đội chỉ có mình người này** (không còn ai quản lý dữ liệu
  khách trong đó).
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

import config
from auth import ratelimit
from auth.models import User, UserConsent
from auth.schemas import ConsentIn, CurrentUser, DeleteAccountIn, UserOut
from database import get_db
from dependencies import get_current_user_allow_unverified

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/me", tags=["privacy"])
CONFIRM_WORD = "XOA"


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()


def record_consent(db: Session, user: User, version: str, source: str) -> None:
    """Gọi trước commit. Chỉ nhận phiên bản hiện hành."""
    if version != config.PRIVACY_POLICY_VERSION:
        raise HTTPException(
            status_code=422,
            detail="Bạn cần đọc và đồng ý Chính sách quyền riêng tư phiên bản mới nhất",
        )
    now = datetime.now(UTC)
    db.add(UserConsent(user_id=user.id, version=version, source=source, accepted_at=now))
    user.privacy_version, user.privacy_accepted_at = version, now


def _active_user(db: Session, user_id: int) -> User:
    row = db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    if row is None:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hiệu lực")
    return row


@router.post("/consent", response_model=UserOut)
def accept_policy(
    body: ConsentIn, user: CurrentUser = Depends(get_current_user_allow_unverified), db: Session = Depends(get_db)
):
    row = _active_user(db, user.id)
    if row.privacy_version != body.version:
        record_consent(db, row, body.version, "prompt")
        db.commit()
    return UserOut.from_user(row)


# ----------------------------------------------------------------------------- tải dữ liệu
@router.get("/export")
def export_my_data(user: CurrentUser = Depends(get_current_user_allow_unverified), db: Session = Depends(get_db)):
    from chat.models import Conversation, Message
    from field.models import FieldPushDevice, FieldWorkspace, FieldWorkspaceMember, ServiceCase, ServiceCaseEvent

    row = _active_user(db, user.id)
    teams = db.execute(
        select(FieldWorkspace.id, FieldWorkspace.name, FieldWorkspaceMember.role)
        .join(FieldWorkspaceMember, FieldWorkspaceMember.workspace_id == FieldWorkspace.id)
        .where(FieldWorkspaceMember.user_id == row.id)
        .order_by(FieldWorkspace.id)
    ).all()
    events = db.scalars(
        select(ServiceCaseEvent).where(ServiceCaseEvent.actor_id == row.id).order_by(ServiceCaseEvent.id)
    ).all()
    assigned = db.scalars(select(ServiceCase).where(ServiceCase.assigned_to == row.id).order_by(ServiceCase.id)).all()
    conversations = []
    for conv in db.scalars(select(Conversation).where(Conversation.user_id == row.id).order_by(Conversation.id)):
        messages = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)).all()
        conversations.append(
            {
                "id": conv.id,
                "title": conv.title,
                "created_at": _iso(conv.created_at),
                "messages": [
                    {"role": m.role, "content": m.content, "created_at": _iso(m.created_at)} for m in messages
                ],
            }
        )
    consents = db.scalars(select(UserConsent).where(UserConsent.user_id == row.id).order_by(UserConsent.id)).all()
    devices = db.scalars(select(FieldPushDevice).where(FieldPushDevice.user_id == row.id)).all()
    data = {
        "exported_at": datetime.now(UTC).isoformat(),
        "note": (
            "Dữ liệu cá nhân của bạn trong FieldOps. Thông tin khách hàng trong các ca thuộc về đội "
            "và do trưởng đội quản lý, nên không nằm trong tệp này."
        ),
        "account": {
            "id": row.id,
            "email": row.email,
            "auth_provider": row.auth_provider,
            "role": row.role,
            "created_at": _iso(row.created_at),
            "email_verified_at": _iso(row.email_verified_at),
            "privacy_version": row.privacy_version,
            "privacy_accepted_at": _iso(row.privacy_accepted_at),
        },
        "consents": [
            {"kind": c.kind, "version": c.version, "source": c.source, "accepted_at": _iso(c.accepted_at)}
            for c in consents
        ],
        "teams": [{"workspace_id": t.id, "name": t.name, "role": t.role} for t in teams],
        "case_activity": [
            {"case_id": e.case_id, "type": e.event_type, "detail": e.detail, "at": _iso(e.created_at)} for e in events
        ],
        "cases_assigned_to_me": [
            {"case_id": c.id, "status": c.status, "scheduled_start": _iso(c.scheduled_start)} for c in assigned
        ],
        "assistant_conversations": conversations,
        "push_devices": [
            {"platform": d.platform, "registered_at": _iso(d.created_at), "last_seen_at": _iso(d.last_seen_at)}
            for d in devices
        ],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": f'attachment; filename="fieldops-du-lieu-cua-toi-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )


# ----------------------------------------------------------------------------- xoá tài khoản
def _impact(db: Session, user_id: int) -> dict:
    from field.models import FieldWorkspace, FieldWorkspaceMember, ServiceCase

    memberships = db.execute(
        select(FieldWorkspaceMember.workspace_id, FieldWorkspaceMember.role, FieldWorkspace.name)
        .join(FieldWorkspace, FieldWorkspace.id == FieldWorkspaceMember.workspace_id)
        .where(FieldWorkspaceMember.user_id == user_id)
        .order_by(FieldWorkspaceMember.workspace_id)
    ).all()
    solo, blocked, leaving = [], [], []
    for workspace_id, role, name in memberships:
        others = db.execute(
            select(FieldWorkspaceMember.role)
            .join(User, User.id == FieldWorkspaceMember.user_id)
            .where(
                FieldWorkspaceMember.workspace_id == workspace_id,
                FieldWorkspaceMember.user_id != user_id,
                User.deleted_at.is_(None),
            )
        ).all()
        if not others:
            cases = db.scalar(select(func.count(ServiceCase.id)).where(ServiceCase.workspace_id == workspace_id))
            solo.append({"id": workspace_id, "name": name, "cases": int(cases or 0)})
        elif role == "owner" and not any(r == "owner" for (r,) in others):
            blocked.append({"id": workspace_id, "name": name})
        else:
            leaving.append({"id": workspace_id, "name": name})
    return {"teams_deleted": solo, "teams_blocked": blocked, "teams_left": leaving}


@router.get("/delete-preview")
def delete_preview(user: CurrentUser = Depends(get_current_user_allow_unverified), db: Session = Depends(get_db)):
    from auth.service import _UNUSABLE_PASSWORD

    row = _active_user(db, user.id)
    return {
        **_impact(db, row.id),
        "requires_password": row.hashed_password != _UNUSABLE_PASSWORD,
        "confirm_word": CONFIRM_WORD,
    }


def _delete_workspace(db: Session, workspace_id: int) -> None:
    """Xoá hẳn một đội và mọi dữ liệu của nó (ca, ảnh, chữ ký, tích hợp…). Chưa commit."""
    from field.models import (
        FieldCaseClosure,
        FieldClientAction,
        FieldGeocodeCache,
        FieldIntegration,
        FieldIntegrationEvent,
        FieldPushOutbox,
        FieldWebhookDelivery,
        FieldWorkspace,
        FieldWorkspaceMember,
        ServiceCase,
        ServiceCaseEvent,
        ServiceCasePhoto,
        ServiceCaseStep,
    )
    from field.photos import remove_photo

    case_ids = select(ServiceCase.id).where(ServiceCase.workspace_id == workspace_id).scalar_subquery()
    for photo in db.scalars(select(ServiceCasePhoto).where(ServiceCasePhoto.case_id.in_(case_ids))).all():
        remove_photo(db, photo)  # xoá cả tệp ảnh trên đĩa
    db.flush()
    integration_ids = select(FieldIntegration.id).where(FieldIntegration.workspace_id == workspace_id).scalar_subquery()
    for statement in (
        delete(FieldCaseClosure).where(FieldCaseClosure.case_id.in_(case_ids)),
        delete(FieldClientAction).where(FieldClientAction.case_id.in_(case_ids)),
        delete(FieldPushOutbox).where(FieldPushOutbox.case_id.in_(case_ids)),
        delete(FieldIntegrationEvent).where(
            or_(FieldIntegrationEvent.integration_id.in_(integration_ids), FieldIntegrationEvent.case_id.in_(case_ids))
        ),
        delete(FieldWebhookDelivery).where(FieldWebhookDelivery.integration_id.in_(integration_ids)),
        delete(FieldIntegration).where(FieldIntegration.workspace_id == workspace_id),
        delete(ServiceCaseStep).where(ServiceCaseStep.case_id.in_(case_ids)),
        delete(ServiceCaseEvent).where(ServiceCaseEvent.case_id.in_(case_ids)),
        delete(ServiceCase).where(ServiceCase.workspace_id == workspace_id),
        delete(FieldGeocodeCache).where(FieldGeocodeCache.workspace_id == workspace_id),
        delete(FieldWorkspaceMember).where(FieldWorkspaceMember.workspace_id == workspace_id),
        delete(FieldWorkspace).where(FieldWorkspace.id == workspace_id),
    ):
        db.execute(statement.execution_options(synchronize_session=False))


def _cleanup_files(user_id: int, workspace_ids: list[int]) -> bool:
    """Sau commit: tài liệu SOP / cá nhân trong Chroma và thư mục upload. Lỗi không làm hỏng việc xoá."""
    ok = True
    try:
        from rag.ingest import delete_user_collection

        delete_user_collection(user_id)
    except Exception:
        ok = False
    for workspace_id in workspace_ids:
        try:
            import chromadb

            chromadb.PersistentClient(path=str(config.CHROMA_DIR)).delete_collection(f"workspace_{workspace_id}")
        except Exception as exc:  # không có tài liệu đội thì Chroma báo NotFound: bình thường
            if "does not exist" not in str(exc).lower() and "notfound" not in type(exc).__name__.lower():
                ok = False
    for path in [config.UPLOAD_DIR / str(user_id)] + [config.UPLOAD_DIR / "workspaces" / str(w) for w in workspace_ids]:
        try:
            if path.exists() and config.UPLOAD_DIR.resolve() in path.resolve().parents:
                shutil.rmtree(path)
        except Exception:
            ok = False
    return ok


@router.post("/delete")
def delete_my_account(
    body: DeleteAccountIn,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user_allow_unverified),
    db: Session = Depends(get_db),
):
    from auth.lifecycle import deactivate_account
    from auth.service import _UNUSABLE_PASSWORD, verify_password
    from auth.session_cookie import clear_session_cookie
    from chat.models import Conversation
    from field.models import FieldPushDevice

    if body.confirm.strip().upper() != CONFIRM_WORD:
        raise HTTPException(status_code=422, detail=f'Gõ "{CONFIRM_WORD}" để xác nhận xoá tài khoản')
    row = _active_user(db, user.id)
    if row.hashed_password != _UNUSABLE_PASSWORD:
        ratelimit.hit("delete:user", str(row.id))
        if not body.password or not verify_password(body.password, row.hashed_password):
            raise HTTPException(status_code=403, detail="Mật khẩu không đúng")
    impact = _impact(db, row.id)
    if impact["teams_blocked"]:
        names = ", ".join(t["name"] for t in impact["teams_blocked"])
        raise HTTPException(
            status_code=409,
            detail=f"Bạn là trưởng đội duy nhất của: {names}. Hãy chuyển quyền trưởng đội cho người khác trước.",
        )
    solo_ids = [t["id"] for t in impact["teams_deleted"]]
    try:
        for workspace_id in solo_ids:
            _delete_workspace(db, workspace_id)
        for conv in db.scalars(select(Conversation).where(Conversation.user_id == row.id)).all():
            db.delete(conv)  # cascade xoá tin nhắn
        db.execute(delete(FieldPushDevice).where(FieldPushDevice.user_id == row.id))
        db.execute(delete(UserConsent).where(UserConsent.user_id == row.id))
        db.execute(
            User.__table__.update()
            .where(User.id == row.id)
            .values(privacy_version=None, privacy_accepted_at=None, email_verified_at=None)
        )
        db.flush()
    except Exception:
        db.rollback()
        raise
    deactivate_account(db, row.id, row.id)  # commit cả giao dịch; lỗi thì rollback tất cả
    files_ok = _cleanup_files(row.id, solo_ids)
    if not files_ok:
        logger.warning("Personal file cleanup pending for deleted user %s", row.id)
    clear_session_cookie(response)
    return {
        "message": "Tài khoản đã được xoá",
        "teams_deleted": [t["name"] for t in impact["teams_deleted"]],
        "cleanup_pending": not files_ok,
    }
