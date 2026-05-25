"""Admin routes — chỉ role=admin."""
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from chat.models import Conversation, Message
from config import UPLOAD_DIR
from database import get_db
from dependencies import require_admin
from rag.ingest import delete_user_collection, list_user_docs

router = APIRouter(prefix="/admin", tags=["admin"])


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
    users = db.query(User).order_by(User.created_at.desc()).all()
    result: list[UserAdminOut] = []
    for u in users:
        docs = await list_user_docs(u.id)
        msg_count = (
            db.query(Message)
            .join(Conversation)
            .filter(Conversation.user_id == u.id)
            .count()
        )
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
    total_users = db.query(User).count()
    total_messages = db.query(Message).count()

    total_docs = 0
    for u in db.query(User).all():
        docs = await list_user_docs(u.id)
        total_docs += len(docs)

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    queries_today = (
        db.query(Message)
        .filter(Message.role == "assistant", Message.created_at >= today_start)
        .count()
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

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    delete_user_collection(user_id)
    user_upload = UPLOAD_DIR / str(user_id)
    if user_upload.exists():
        shutil.rmtree(user_upload)

    db.delete(user)
    db.commit()
    return {"message": f"Đã xóa user {user.email}"}
