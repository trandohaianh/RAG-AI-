"""Pydantic schemas cho chat."""
from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = Field(default="Cuộc trò chuyện mới", max_length=255)


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    sources: list[str] = []
    rating: int | None = None
    eval_faithfulness: float | None = None
    eval_relevancy: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    source: str | None = None
    """Tên file cụ thể để chỉ hỏi trong phạm vi 1 tài liệu — None = tìm trên toàn bộ kho."""


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1)
