"""Embed widget — token + chat + widget.js (tuần 5–6)."""
import json
import logging
from contextlib import aclosing
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth.models import User
from chat import service as chat_service
from chat.models import Conversation
from config import API_PUBLIC_URL, APP_PUBLIC_URL, GEMINI_API_KEY, LLM_PROVIDER
from database import SessionLocal, get_db
from dependencies import get_current_user
from auth.schemas import CurrentUser
from saas.limits import enforce_query_quota, release_query_quota
from saas.models import EmbedGuestSession, EmbedToken
from saas.security import generate_embed_token, hash_secret
from rag.chain import RAG_ERROR_MESSAGE, stream_rag_answer
from rag.evidence import public_bundle

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
    conversation_id: int | None = Field(default=None, gt=0)
    session_token: str | None = Field(default=None, min_length=20, max_length=200)


def _get_embed_row(db: Session, raw_token: str) -> EmbedToken:
    h = hash_secret(raw_token)
    row = (
        db.query(EmbedToken)
        .join(User, User.id == EmbedToken.user_id)
        .filter(
            EmbedToken.token_hash == h,
            EmbedToken.active.is_(True),
            User.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=401, detail="Embed token không hợp lệ")
    return row


def _origin_of(url: str | None) -> str:
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    return f"{parsed.scheme}://{parsed.netloc}".lower() if parsed.scheme and parsed.netloc else ""


def _check_origin(row: EmbedToken, origin: str | None, parent_origin: str | None = None) -> None:
    """Giới hạn website được dùng token.

    - Website khách gọi API trực tiếp: Origin phải thuộc allowed_origins.
    - Widget chuẩn: request đi từ iframe `APP_PUBLIC_URL/embed`, nên Origin là origin của
      chính ứng dụng; khi đó origin của TRANG CHA (do trang embed đọc từ ancestorOrigins /
      referrer) mới là thứ phải thuộc allowed_origins.
    """
    if not row.allowed_origins or not row.allowed_origins.strip():
        return
    if not origin:
        raise HTTPException(status_code=403, detail="Thiếu Origin header")
    allowed = {_origin_of(o) or o.strip().rstrip("/").lower()
               for o in row.allowed_origins.split(",") if o.strip()}
    origin_norm = _origin_of(origin) or origin.rstrip("/").lower()
    if origin_norm in allowed:
        return
    app_origin = _origin_of(APP_PUBLIC_URL)
    if app_origin and origin_norm == app_origin and _origin_of(parent_origin) in allowed:
        return
    raise HTTPException(status_code=403, detail="Origin không được phép")


async def _embed_user_plan(db: Session, user_id: int) -> str:
    u = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not u:
        raise HTTPException(status_code=401, detail="Embed token không hợp lệ")
    return u.subscription_plan or "free"


def _guest_conversation(
    db: Session, row: EmbedToken, body: EmbedChatIn
) -> tuple[int, str]:
    if body.conversation_id is not None or body.session_token is not None:
        if body.conversation_id is None or body.session_token is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên chat")
        guest = (
            db.query(EmbedGuestSession)
            .join(Conversation, Conversation.id == EmbedGuestSession.conversation_id)
            .filter(
                EmbedGuestSession.token_hash == hash_secret(body.session_token),
                EmbedGuestSession.embed_token_id == row.id,
                EmbedGuestSession.conversation_id == body.conversation_id,
                EmbedGuestSession.expires_at > datetime.now(timezone.utc),
                Conversation.user_id == row.user_id,
            )
            .first()
        )
        if not guest:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên chat")
        return guest.conversation_id, body.session_token
    conv = Conversation(user_id=row.user_id, title="Embed chat")
    db.add(conv)
    db.flush()
    raw_session = secrets.token_urlsafe(32)
    db.add(
        EmbedGuestSession(
            token_hash=hash_secret(raw_session),
            embed_token_id=row.id,
            conversation_id=conv.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
    )
    db.commit()
    return conv.id, raw_session


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
    db.query(EmbedGuestSession).filter(
        EmbedGuestSession.embed_token_id == row.id
    ).delete(synchronize_session=False)
    db.commit()
    return {"message": "Đã vô hiệu hóa embed token"}


@router.post("/chat")
async def embed_chat(
    body: EmbedChatIn,
    request: Request,
    x_embed_token: str = Header(..., alias="X-Embed-Token"),
    db: Session = Depends(get_db),
):
    if LLM_PROVIDER != "ollama" and not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Thiếu GEMINI_API_KEY")

    row = _get_embed_row(db, x_embed_token)
    _check_origin(row, request.headers.get("origin"), request.headers.get("x-embed-parent-origin"))

    plan = await _embed_user_plan(db, row.user_id)
    conv_id = None
    session_token = None
    if body.conversation_id is not None or body.session_token is not None:
        # Check an existing guest before charging; create new guests only after
        # admission so quota rejection cannot create unbounded empty sessions.
        conv_id, session_token = _guest_conversation(db, row, body)
    from costs.budget import enforce_budget

    budget = enforce_budget(db, row.user_id)
    try:
        reservation = enforce_query_quota(db, row.user_id, plan)
    except Exception:
        budget.release()
        raise
    try:
        if conv_id is None:
            conv_id, session_token = _guest_conversation(db, row, body)
        chat_service.add_message(db, conv_id, "user", body.question)
        history = chat_service.get_history_for_rag(db, conv_id)
    except Exception:
        release_query_quota(db, reservation)
        budget.release()
        raise
    user_id = row.user_id
    question = body.question
    history_snapshot = list(history)
    full_answer: list[str] = []
    sources: list[str] = []
    evidence_bundle: dict = {}

    async def event_generator():
        nonlocal sources
        from costs.context import set_request_usage

        set_request_usage(user_id=user_id, feature="embed_chat")
        try:
            async with aclosing(stream_rag_answer(user_id, question, history_snapshot, include_evidence=True)) as answer_stream:
                async for kind, payload in answer_stream:
                    if kind == "evidence":
                        evidence_bundle.update(payload)
                    elif kind == "token":
                        full_answer.append(payload)  # type: ignore[arg-type]
                        yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"
                    elif kind == "done":
                        sources = payload  # type: ignore[assignment]
                        db_save = SessionLocal()
                        try:
                            live_token = _get_embed_row(db_save, x_embed_token)
                            _guest_conversation(
                                db_save,
                                live_token,
                                EmbedChatIn(
                                    question=question,
                                    conversation_id=conv_id,
                                    session_token=session_token,
                                ),
                            )
                            chat_service.add_message(
                                db_save, conv_id, "assistant", "".join(full_answer), sources, **({"evidence": evidence_bundle} if evidence_bundle else {})
                            )
                        finally:
                            db_save.close()
                        yield f"data: {json.dumps({'done': True, 'sources': sources, 'conversation_id': conv_id, 'session_token': session_token, **public_bundle(evidence_bundle)}, ensure_ascii=False)}\n\n"
                        return
        except Exception as exc:
            logging.getLogger(__name__).warning("RAG request failed (%s)", type(exc).__name__)
            yield f"data: {json.dumps({'error': RAG_ERROR_MESSAGE, 'code': 'rag_unavailable'}, ensure_ascii=False)}\n\n"
            return
        finally:
            budget.release()   # chi phí thật đã được ghi; trả khoản đặt trước

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
