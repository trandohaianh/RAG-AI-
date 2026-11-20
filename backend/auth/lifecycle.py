"""Revoke an account while retaining stable identities for case audit."""

import secrets
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from auth.models import PasswordResetToken, User
from field.models import (
    FieldIntegration,
    FieldWorkspace,
    FieldWorkspaceMember,
    ServiceCase,
    ServiceCaseEvent,
)
from saas.models import ApiKey, EmbedGuestSession, EmbedToken


def deactivate_account(db: Session, user_id: int, actor_id: int) -> None:
    try:
        # Acquire the writer lock before checking ownership on SQLite. On
        # PostgreSQL this locks the identity row; workspace rows below serialize
        # concurrent owner removals within each team.
        locked = db.execute(
            update(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .values(token_version=User.token_version)
            .execution_options(synchronize_session=False)
        )
        if locked.rowcount != 1:
            raise HTTPException(status_code=404, detail="Không tìm thấy user")
        stripe_customer_id = db.scalar(select(User.stripe_customer_id).where(User.id == user_id))
        workspace_ids = db.scalars(
            select(FieldWorkspaceMember.workspace_id)
            .where(
                FieldWorkspaceMember.user_id == user_id,
                FieldWorkspaceMember.role == "owner",
            )
            .order_by(FieldWorkspaceMember.workspace_id)
        ).all()
        if workspace_ids:
            db.scalars(
                select(FieldWorkspace)
                .where(FieldWorkspace.id.in_(workspace_ids))
                .order_by(FieldWorkspace.id)
                .with_for_update()
            ).all()
        for workspace_id in workspace_ids:
            others = db.scalars(
                select(FieldWorkspaceMember)
                .join(User, User.id == FieldWorkspaceMember.user_id)
                .where(
                    FieldWorkspaceMember.workspace_id == workspace_id,
                    FieldWorkspaceMember.user_id != user_id,
                    User.deleted_at.is_(None),
                )
            ).all()
            if others and not any(member.role == "owner" for member in others):
                raise HTTPException(
                    status_code=409,
                    detail="Cần chuyển quyền trưởng đội trước khi vô hiệu hóa tài khoản này",
                )

        embed_ids = select(EmbedToken.id).where(EmbedToken.user_id == user_id)
        db.execute(delete(EmbedGuestSession).where(EmbedGuestSession.embed_token_id.in_(embed_ids)))
        db.execute(update(EmbedToken).where(EmbedToken.user_id == user_id).values(active=False))
        db.execute(delete(ApiKey).where(ApiKey.user_id == user_id))
        db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        db.execute(delete(FieldWorkspaceMember).where(FieldWorkspaceMember.user_id == user_id))
        # Ca mới từ CRM/NMS không được tự giao cho người đã rời mọi đội.
        db.execute(
            update(FieldIntegration)
            .where(FieldIntegration.default_assignee == user_id)
            .values(default_assignee=None)
            .execution_options(synchronize_session=False)
        )

        for case in db.scalars(select(ServiceCase).where(ServiceCase.assigned_to == user_id)).all():
            case.assigned_to = None
            db.add(
                ServiceCaseEvent(
                    case_id=case.id,
                    actor_id=actor_id,
                    event_type="assigned",
                    detail="Người phụ trách đã bị vô hiệu hóa tài khoản",
                )
            )
        db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                email=f"deleted-{user_id}-{secrets.token_hex(16)}@deleted.invalid",
                hashed_password="!deleted",
                # Giải phóng liên kết Google để người đó có thể tạo tài khoản mới sau này.
                google_sub=None,
                role="user",
                subscription_plan="free",
                token_version=User.token_version + 1,
                deleted_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.expire_all()
    except Exception:
        db.rollback()
        raise
    # Sau khi commit (không giữ transaction trong lúc gọi mạng): tài khoản đã bị vô hiệu
    # hoá thì Stripe không được tiếp tục trừ tiền khách.
    if stripe_customer_id:
        import logging

        try:
            from billing.router import cancel_customer_subscriptions

            cancel_customer_subscriptions(stripe_customer_id)
        except Exception as exc:  # không làm hỏng việc vô hiệu hoá đã commit
            logging.getLogger(__name__).warning(
                "Không huỷ được subscription Stripe của user %s (%s); cần huỷ thủ công",
                user_id,
                type(exc).__name__,
            )
