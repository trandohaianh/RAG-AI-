"""User identities and single-use password reset credentials."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")
    subscription_plan: Mapped[str] = mapped_column(String(20), default="free")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # "password" | "google" — cách tạo tài khoản lần đầu (vẫn có thể đặt mật khẩu sau).
    auth_provider: Mapped[str] = mapped_column(String(20), default="password", server_default="password")
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_sync_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Phiên bản Chính sách quyền riêng tư người dùng đã đồng ý gần nhất (lịch sử ở user_consents).
    privacy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserConsent(Base):
    """Nhật ký đồng ý (Nghị định 356/2025: sự đồng ý phải lưu lại và kiểm chứng được thời điểm,
    nội dung). Mỗi lần đồng ý một phiên bản chính sách là một dòng; không sửa dòng cũ."""

    __tablename__ = "user_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="privacy_policy")
    version: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))  # register | google | prompt
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignupInvite(Base):
    """Lời mời tạo tài khoản (F1-A, SIGNUP_MODE=invite).

    Chỉ lưu SHA-256 của mã (mã gốc hiện cho quản trị đúng một lần lúc tạo). Dùng một lần:
    used_at đặt trong CÙNG transaction tạo tài khoản, bằng UPDATE có điều kiện, nên hai người
    dùng chung một mã cùng lúc thì chỉ một người thành công.
    """

    __tablename__ = "signup_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Để trống = ai có mã cũng dùng được; có giá trị = chỉ đăng ký đúng email này.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
