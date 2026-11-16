"""Pydantic schemas cho auth."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class PasswordInput(BaseModel):
    password: str = Field(..., min_length=6, max_length=72)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Mật khẩu tối đa 72 byte UTF-8")
        return value


class UserCreate(PasswordInput):
    email: EmailStr


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(PasswordInput):
    token: str = Field(max_length=2048)


class CurrentUser(BaseModel):
    """User đã xác thực — không gắn SQLAlchemy session."""

    id: int
    email: str
    role: str
    subscription_plan: str = "free"
    email_verified: bool = True


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    subscription_plan: str = "free"
    auth_provider: str = "password"
    email_verified: bool = False
    has_google: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user) -> "UserOut":
        return cls(
            id=user.id, email=user.email, role=user.role,
            subscription_plan=user.subscription_plan or "free",
            auth_provider=getattr(user, "auth_provider", None) or "password",
            email_verified=getattr(user, "email_verified_at", None) is not None,
            has_google=bool(getattr(user, "google_sub", None)),
            created_at=user.created_at,
        )


class GoogleLoginRequest(BaseModel):
    credential: str = Field(..., min_length=20, max_length=4096)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., max_length=2048)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool = False
