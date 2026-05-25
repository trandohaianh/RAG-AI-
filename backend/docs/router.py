"""Routes quản lý tài liệu: upload, list, delete."""
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from auth.schemas import CurrentUser
from config import ALLOWED_EXTENSIONS, UPLOAD_DIR
from dependencies import get_current_user
from rag.ingest import delete_doc, format_ingest_error, ingest_file, list_user_docs
from rag.suggest import suggest_questions_for_user

router = APIRouter(prefix="/documents", tags=["documents"])


class DocItem(BaseModel):
    name: str
    chunks: int
    uploaded_at: str | None = None


class SuggestedQuestionsOut(BaseModel):
    questions: list[str]


class UploadResult(BaseModel):
    message: str
    chunks: int
    filename: str


@router.get("/suggested-questions", response_model=SuggestedQuestionsOut)
async def suggested_questions(
    user: CurrentUser = Depends(get_current_user),
    limit: int = 5,
):
    """Tuần 3–4: gợi ý câu hỏi dựa trên chunk đã index."""
    q = await suggest_questions_for_user(user.id, limit=min(max(limit, 1), 10))
    return SuggestedQuestionsOut(questions=q)


def _user_upload_dir(user_id: int) -> Path:
    path = UPLOAD_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/upload", response_model=UploadResult)
async def upload_doc(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ hỗ trợ: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    dest = _user_upload_dir(user.id) / file.filename
    content = await file.read()
    dest.write_bytes(content)

    try:
        chunks = await ingest_file(user.id, dest)
    except Exception as exc:
        status = 429 if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc) else 500
        raise HTTPException(status_code=status, detail=format_ingest_error(exc)) from exc

    return UploadResult(
        filename=file.filename,
        chunks=chunks,
        message=f"{chunks} chunks đã được lập chỉ mục",
    )


@router.get("", response_model=list[DocItem])
async def list_docs(user: CurrentUser = Depends(get_current_user)):
    docs = await list_user_docs(user.id)
    user_dir = _user_upload_dir(user.id)
    result: list[DocItem] = []
    for d in docs:
        fpath = user_dir / d["name"]
        uploaded_at = None
        if fpath.exists():
            ts = fpath.stat().st_mtime
            uploaded_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        result.append(
            DocItem(name=d["name"], chunks=d["chunks"], uploaded_at=uploaded_at)
        )
    return result


@router.delete("/{filename}")
async def remove_doc(
    filename: str,
    user: CurrentUser = Depends(get_current_user),
):
    deleted = await delete_doc(user.id, filename)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")

    fpath = _user_upload_dir(user.id) / filename
    if fpath.exists():
        fpath.unlink()

    try:
        from rag.cache import invalidate_user_cache

        invalidate_user_cache(user.id)
    except Exception:
        pass

    return {"message": f"Đã xóa {filename}", "deleted_chunks": deleted}
