"""Admin routes — chỉ role=admin."""

import logging
import shutil
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.lifecycle import deactivate_account
from auth.models import User
from auth.schemas import CurrentUser
from chat.models import Conversation, Message
from config import UPLOAD_DIR
from database import get_db
from dependencies import require_admin
from rag.ingest import delete_user_collection, list_user_docs
from saas.models import DailyQueryUsage

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


class UserAdminOut(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime
    doc_count: int
    message_count: int

    model_config = {"from_attributes": True}


class StatsOut(BaseModel):
    total_users: int
    total_docs: int
    total_messages: int
    queries_today: int


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).filter(User.deleted_at.is_(None)).order_by(User.created_at.desc()).all()
    result: list[UserAdminOut] = []
    for u in users:
        docs = await list_user_docs(u.id)
        msg_count = db.query(Message).join(Conversation).filter(Conversation.user_id == u.id).count()
        result.append(
            UserAdminOut(
                id=u.id,
                email=u.email,
                role=u.role,
                created_at=u.created_at,
                doc_count=len(docs),
                message_count=msg_count,
            )
        )
    return result


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    total_users = db.query(User).filter(User.deleted_at.is_(None)).count()
    active_messages = db.query(Message).join(Conversation).join(User).filter(User.deleted_at.is_(None))
    total_messages = active_messages.count()

    total_docs = 0
    for u in db.query(User).filter(User.deleted_at.is_(None)).all():
        docs = await list_user_docs(u.id)
        total_docs += len(docs)

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    queries_today = (
        db.query(func.coalesce(func.sum(DailyQueryUsage.used_queries), 0))
        .join(User, User.id == DailyQueryUsage.user_id)
        .filter(
            User.deleted_at.is_(None),
            DailyQueryUsage.usage_date == today_start.date(),
        )
        .scalar()
    )

    return StatsOut(
        total_users=total_users,
        total_docs=total_docs,
        total_messages=total_messages,
        queries_today=queries_today,
    )


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Không thể xóa chính mình")

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    deactivate_account(db, user_id, admin.id)
    # Revoke database access before optional file cleanup. A storage outage must
    # never leave the account or its public credentials active.
    cleanup_pending = False
    try:
        delete_user_collection(user_id)
        user_upload = UPLOAD_DIR / str(user_id)
        if user_upload.exists():
            if user_upload.resolve().parent != UPLOAD_DIR.resolve():
                raise ValueError("Upload path escapes storage root")
            shutil.rmtree(user_upload)
    except Exception:
        cleanup_pending = True
        logger.warning("Personal document cleanup failed for disabled user %s", user_id)
    return {
        "message": "Tài khoản đã bị vô hiệu hóa và thu hồi quyền truy cập",
        "cleanup_pending": cleanup_pending,
    }


# ------------------------------------------------------------------ lời mời (F1-A)
class InviteCreateIn(BaseModel):
    email: EmailStr | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    note: str | None = Field(default=None, max_length=200)


class InviteOut(BaseModel):
    id: int
    email: str | None
    note: str | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_by: int | None
    revoked_at: datetime | None
    status: str


class InviteCreatedOut(InviteOut):
    # Mã gốc chỉ trả về MỘT lần lúc tạo; máy chủ chỉ giữ SHA-256.
    code: str
    signup_url: str


def _invite_out(invite) -> dict:
    from auth.invites import invite_status

    return {
        "id": invite.id,
        "email": invite.email,
        "note": invite.note,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
        "used_at": invite.used_at,
        "used_by": invite.used_by,
        "revoked_at": invite.revoked_at,
        "status": invite_status(invite),
    }


@router.post("/invites", response_model=InviteCreatedOut)
def create_signup_invite(
    data: InviteCreateIn,
    admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    from auth.invites import create_invite
    from config import APP_PUBLIC_URL

    try:
        invite, code = create_invite(db, created_by=admin.id, email=data.email, days=data.days, note=data.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("Admin %s created signup invite %s", admin.id, invite.id)
    return {**_invite_out(invite), "code": code, "signup_url": f"{APP_PUBLIC_URL}/register?invite={quote(code)}"}


@router.get("/invites", response_model=list[InviteOut])
def list_signup_invites(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from auth.models import SignupInvite

    rows = db.query(SignupInvite).order_by(SignupInvite.id.desc()).limit(200).all()
    return [_invite_out(r) for r in rows]


@router.delete("/invites/{invite_id}")
def revoke_signup_invite(
    invite_id: int,
    admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from auth.invites import revoke_invite

    if not revoke_invite(db, invite_id):
        raise HTTPException(status_code=404, detail="Không có lời mời đang hiệu lực với mã số này")
    logger.info("Admin %s revoked signup invite %s", admin.id, invite_id)
    return {"message": "Đã thu hồi lời mời"}
