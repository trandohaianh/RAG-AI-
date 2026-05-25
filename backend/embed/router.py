"""Embed widget — token + chat + widget.js (tuần 5–6)."""
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth.models import User
from chat import service as chat_service
from config import API_PUBLIC_URL, APP_PUBLIC_URL, GEMINI_API_KEY
from database import SessionLocal, get_db
from dependencies import get_current_user
from auth.schemas import CurrentUser
from saas.limits import enforce_query_quota
from saas.models import EmbedToken
from saas.security import generate_embed_token, hash_secret
from rag.chain import stream_rag_answer

router = APIRouter(prefix="/embed", tags=["embed"])


class EmbedTokenCreate(BaseModel):
    label: str = Field(default="Website", max_length=120)
    allowed_origins: str | None = Field(
        default=None,
        description="Danh sách origin cách nhau bởi dấu phẩy, ví dụ https://mysite.com",
    )


class EmbedTokenOut(BaseModel):
    id: int
    label: str
    allowed_origins: str | None
    active: bool
    created_at: str
    snippet: str | None = None


class EmbedTokenCreated(EmbedTokenOut):
    token: str


class EmbedChatIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    conversation_id: int | None = None


def _get_embed_row(db: Session, raw_token: str) -> EmbedToken:
    h = hash_secret(raw_token)
    row = (
        db.query(EmbedToken)
        .filter(EmbedToken.token_hash == h, EmbedToken.active.is_(True))
        .first()
    )
    if not row:
        raise HTTPException(status_code=401, detail="Embed token không hợp lệ")
    return row


def _check_origin(row: EmbedToken, origin: str | None) -> None:
    if not row.allowed_origins or not row.allowed_origins.strip():
        return
    if not origin:
        raise HTTPException(status_code=403, detail="Thiếu Origin header")
    allowed = {o.strip().rstrip("/") for o in row.allowed_origins.split(",") if o.strip()}
    origin_norm = origin.rstrip("/")
    if origin_norm not in allowed:
        raise HTTPException(status_code=403, detail="Origin không được phép")


async def _embed_user_plan(db: Session, user_id: int) -> str:
    u = db.query(User).filter(User.id == user_id).first()
    return (u.subscription_plan if u else None) or "free"


@router.post("/tokens", response_model=EmbedTokenCreated)
def create_embed_token(
    body: EmbedTokenCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw, h = generate_embed_token()
    row = EmbedToken(
        user_id=user.id,
        label=body.label,
        token_hash=h,
        allowed_origins=body.allowed_origins,
        active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    snippet = (
        f'<script src="{API_PUBLIC_URL}/embed/widget.js" '
        f'data-token="{raw}" data-api="{API_PUBLIC_URL}"></script>'
    )
    return EmbedTokenCreated(
        id=row.id,
        label=row.label,
        allowed_origins=row.allowed_origins,
        active=row.active,
        created_at=row.created_at.isoformat(),
        token=raw,
        snippet=snippet,
    )


@router.get("/tokens", response_model=list[EmbedTokenOut])
def list_embed_tokens(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(EmbedToken)
        .filter(EmbedToken.user_id == user.id)
        .order_by(EmbedToken.created_at.desc())
        .all()
    )
    return [
        EmbedTokenOut(
            id=r.id,
            label=r.label,
            allowed_origins=r.allowed_origins,
            active=r.active,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.delete("/tokens/{token_id}")
def revoke_embed_token(
    token_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(EmbedToken)
        .filter(EmbedToken.id == token_id, EmbedToken.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy token")
    row.active = False
    db.commit()
    return {"message": "Đã vô hiệu hóa embed token"}


@router.post("/chat")
async def embed_chat(
    body: EmbedChatIn,
    request: Request,
    x_embed_token: str = Header(..., alias="X-Embed-Token"),
    db: Session = Depends(get_db),
):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Thiếu GEMINI_API_KEY")

    row = _get_embed_row(db, x_embed_token)
    _check_origin(row, request.headers.get("origin"))

    plan = await _embed_user_plan(db, row.user_id)
    enforce_query_quota(db, row.user_id, plan)

    if body.conversation_id:
        conv = chat_service.get_conversation(db, row.user_id, body.conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")
        conv_id = conv.id
    else:
        conv = chat_service.create_conversation(db, row.user_id, "Embed chat")
        conv_id = conv.id

    chat_service.add_message(db, conv_id, "user", body.question)
    history = chat_service.get_history_for_rag(db, conv_id)
    user_id = row.user_id
    question = body.question
    history_snapshot = list(history)
    full_answer: list[str] = []
    sources: list[str] = []

    async def event_generator():
        nonlocal sources
        try:
            async for kind, payload in stream_rag_answer(user_id, question, history_snapshot):
                if kind == "token":
                    full_answer.append(payload)  # type: ignore[arg-type]
                    yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"
                elif kind == "done":
                    sources = payload  # type: ignore[assignment]
                    yield f"data: {json.dumps({'done': True, 'sources': sources, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return

        answer_text = "".join(full_answer)
        db_save = SessionLocal()
        try:
            chat_service.add_message(db_save, conv_id, "assistant", answer_text, sources)
        finally:
            db_save.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
        },
    )


@router.get("/widget.js", response_class=PlainTextResponse)
def embed_widget_js():
    """Script nhúng vào website bên thứ ba."""
    app_url = APP_PUBLIC_URL.rstrip("/")
    js = f"""(function() {{
  var s = document.currentScript;
  if (!s) return;
  var token = s.getAttribute("data-token");
  var api = (s.getAttribute("data-api") || "{API_PUBLIC_URL}").replace(/\\/$/, "");
  var app = (s.getAttribute("data-app") || "{app_url}").replace(/\\/$/, "");
  if (!token) {{ console.error("[RAG Embed] Thiếu data-token"); return; }}
  var btn = document.createElement("button");
  btn.textContent = "💬 Hỏi AI";
  btn.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:99999;padding:12px 18px;border-radius:999px;border:none;background:#2563eb;color:#fff;font:600 14px system-ui;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.2)";
  var iframe = document.createElement("iframe");
  iframe.style.cssText = "display:none;position:fixed;bottom:80px;right:20px;width:380px;height:520px;max-width:calc(100vw - 40px);max-height:calc(100vh - 100px);z-index:99998;border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.15);background:#fff";
  iframe.src = app + "/embed?token=" + encodeURIComponent(token) + "&api=" + encodeURIComponent(api);
  btn.onclick = function() {{
    iframe.style.display = iframe.style.display === "none" ? "block" : "none";
  }};
  document.body.appendChild(btn);
  document.body.appendChild(iframe);
}})();
"""
    return PlainTextResponse(js, media_type="application/javascript")
