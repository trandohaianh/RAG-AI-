"""Pydantic schemas cho auth."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=6)


class CurrentUser(BaseModel):
    """User đã xác thực — không gắn SQLAlchemy session."""

    id: int
    email: str
    role: str
    subscription_plan: str = "free"


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    subscription_plan: str = "free"
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"
