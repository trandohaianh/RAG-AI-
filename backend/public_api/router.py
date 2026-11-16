"""Public REST API v1 + API key management — tuần 7–8."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from config import GEMINI_API_KEY, LLM_PROVIDER
from database import get_db
from dependencies import get_current_user
from saas.limits import enforce_query_quota
from saas.models import ApiKey
from saas.security import generate_api_key, hash_secret
from rag.chain import RagGenerationError, answer_rag_once
from rag.evidence import EvidenceOut, CitationAudit, public_bundle
from rag.ingest import list_user_docs

router = APIRouter(tags=["public-api"])
v1 = APIRouter(prefix="/v1", tags=["v1"])


class ApiKeyCreate(BaseModel):
    name: str = Field(default="Production", max_length=120)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None


class ApiKeyCreated(ApiKeyOut):
    key: str


class V1AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)


class V1AskOut(BaseModel):
    answer: str
    sources: list[str]
    evidence: list[EvidenceOut] = Field(default_factory=list)
    citation_audit: CitationAudit | None = None


async def get_api_key_user(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> User:
    h = hash_secret(x_api_key)
    row = db.query(ApiKey).filter(ApiKey.key_hash == h).first()
    if not row:
        raise HTTPException(status_code=401, detail="API key không hợp lệ")
    user = db.query(User).filter(User.id == row.user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User không tồn tại")
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return user


@router.post("/api-keys", response_model=ApiKeyCreated)
def create_api_key(
    body: ApiKeyCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(ApiKey).filter(ApiKey.user_id == user.id).count()
    if existing >= 10:
        raise HTTPException(status_code=400, detail="Tối đa 10 API keys")
    raw, h, prefix = generate_api_key()
    row = ApiKey(user_id=user.id, name=body.name, key_prefix=prefix, key_hash=h)
    db.add(row)
    db.commit()
    db.refresh(row)
    return ApiKeyCreated(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        created_at=row.created_at.isoformat(),
        last_used_at=None,
        key=raw,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return [
        ApiKeyOut(
            id=r.id,
            name=r.name,
            key_prefix=r.key_prefix,
            created_at=r.created_at.isoformat(),
            last_used_at=r.last_used_at.isoformat() if r.last_used_at else None,
        )
        for r in rows
    ]


@router.delete("/api-keys/{key_id}")
def delete_api_key(
    key_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy key")
    db.delete(row)
    db.commit()
    return {"message": "Đã xóa API key"}


@v1.post("/ask", response_model=V1AskOut)
async def v1_ask(
    body: V1AskIn,
    db: Session = Depends(get_db),
    api_user: User = Depends(get_api_key_user),
):
    if LLM_PROVIDER != "ollama" and not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Thiếu GEMINI_API_KEY")
    plan = api_user.subscription_plan or "free"
    from costs.budget import enforce_budget
    from costs.context import set_request_usage

    with enforce_budget(db, api_user.id):
        set_request_usage(user_id=api_user.id, feature="public_api")
        enforce_query_quota(db, api_user.id, plan)
        evidence_bundle = {}
        try:
            answer, sources = await answer_rag_once(api_user.id, body.question, evidence_out=evidence_bundle)
        except RagGenerationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
    from chat import service as chat_service

    conv = chat_service.get_or_create_conversation(db, api_user.id, "__api__")
    chat_service.add_message(db, conv.id, "user", body.question)
    chat_service.add_message(db, conv.id, "assistant", answer, sources, evidence=evidence_bundle)
    return V1AskOut(answer=answer, sources=sources, **public_bundle(evidence_bundle))


@v1.get("/documents")
async def v1_documents(api_user: User = Depends(get_api_key_user)):
    docs = await list_user_docs(api_user.id)
    return [
        {"name": d["name"], "chunks": d["chunks"], "uploaded_at": d.get("uploaded_at")}
        for d in docs
    ]


router.include_router(v1)
