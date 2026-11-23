"""Lời mời tạo tài khoản (F1-A): quản trị tạo/thu hồi; đăng ký tiêu thụ mã một lần.

Mã là chuỗi ngẫu nhiên `secrets.token_urlsafe(18)` (~144 bit), chỉ lưu SHA-256. Tiêu thụ bằng
UPDATE có điều kiện trong cùng transaction tạo tài khoản (giống mã đặt lại mật khẩu), nên mã
hết hạn / đã thu hồi / đã dùng / sai email đều bị từ chối như nhau, và dùng song song chỉ một
người thắng.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from auth.models import SignupInvite

INVALID_INVITE_MESSAGE = "Mã mời không hợp lệ, đã dùng hoặc đã hết hạn"
INVITE_REQUIRED_MESSAGE = "Hệ thống chỉ nhận đăng ký bằng lời mời — hãy nhập mã mời do quản trị gửi"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def signup_requires_invite() -> bool:
    import config

    return config.SIGNUP_MODE == "invite"


def create_invite(
    db: Session, *, created_by: int | None, email: str | None = None, days: int | None = None, note: str | None = None
) -> tuple[SignupInvite, str]:
    import config

    days = config.INVITE_DEFAULT_DAYS if days is None else days
    if not 1 <= days <= config.INVITE_MAX_DAYS:
        raise ValueError(f"Hạn lời mời phải từ 1 đến {config.INVITE_MAX_DAYS} ngày")
    code = secrets.token_urlsafe(18)
    now = datetime.now(UTC)
    invite = SignupInvite(
        code_hash=hash_code(code),
        email=(email or "").strip() or None,
        note=(note or "").strip() or None,
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(days=days),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite, code


def revoke_invite(db: Session, invite_id: int) -> bool:
    """Thu hồi lời mời chưa dùng. Trả về False nếu không có hoặc đã dùng/đã thu hồi."""
    changed = db.execute(
        update(SignupInvite)
        .where(SignupInvite.id == invite_id, SignupInvite.used_at.is_(None), SignupInvite.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return changed.rowcount == 1


def claim_invite(db: Session, code: str, email: str, user_id: int) -> bool:
    """Đánh dấu mã đã dùng cho `user_id`. KHÔNG commit: người gọi commit cùng tài khoản mới."""
    if not code or not code.strip():
        return False
    now = datetime.now(UTC)
    claimed = db.execute(
        update(SignupInvite)
        .where(
            SignupInvite.code_hash == hash_code(code),
            SignupInvite.used_at.is_(None),
            SignupInvite.revoked_at.is_(None),
            SignupInvite.expires_at > now,
            or_(SignupInvite.email.is_(None), func.lower(SignupInvite.email) == email.strip().lower()),
        )
        .values(used_at=now, used_by=user_id)
        .execution_options(synchronize_session=False)
    )
    return claimed.rowcount == 1


def invite_status(invite: SignupInvite, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    expires = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=UTC)
    if invite.used_at is not None:
        return "used"
    if invite.revoked_at is not None:
        return "revoked"
    if expires <= now:
        return "expired"
    return "active"


def require_invite_for_new_user(db: Session, user_id: int, email: str, code: str | None) -> None:
    """Gọi sau `flush()` tài khoản mới, trước commit. Ném ValueError nếu chế độ invite từ chối.

    Tài khoản đầu tiên (id=1, thành admin) được tạo không cần mã để khởi tạo hệ thống —
    cùng quy tắc với cấp quyền admin, nên hai đăng ký song song trên DB rỗng không cùng lọt.
    """
    if not signup_requires_invite():
        if code:
            # Chế độ open vẫn tiêu thụ mã nếu người dùng nhập (để danh sách lời mời đúng trạng thái).
            claim_invite(db, code, email, user_id)
        return
    if user_id == 1:
        if code:
            claim_invite(db, code, email, user_id)
        return
    if not code or not code.strip():
        raise InviteRequired(INVITE_REQUIRED_MESSAGE)
    if not claim_invite(db, code, email, user_id):
        raise InviteInvalid(INVALID_INVITE_MESSAGE)


class InviteRequired(ValueError):
    pass


class InviteInvalid(ValueError):
    pass
