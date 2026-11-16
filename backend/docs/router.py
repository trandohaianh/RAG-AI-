"""Routes quản lý tài liệu: upload, list, delete."""
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.schemas import CurrentUser
from config import ALLOWED_EXTENSIONS, UPLOAD_DIR
from database import get_db
from dependencies import get_current_user
from field.models import FieldWorkspaceMember
from pipeline.lineage import get_lineage_map
from rag.ingest import delete_doc, format_ingest_error, ingest_file, list_user_docs
from rag.suggest import suggest_questions_for_user

router = APIRouter(prefix="/documents", tags=["documents"])


class DocItem(BaseModel):
    name: str
    chunks: int
    uploaded_at: str | None = None


class LineageStatus(BaseModel):
    medallion_enabled: bool
    reachable: bool = True


class LineageItem(BaseModel):
    bronze_ingested_at: str | None = None
    silver_chunk_count: int = 0
    validation_passed: bool | None = None
    gold_ready: bool = False
    gold_chunk_count: int | None = None


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
    from costs.context import set_request_usage

    set_request_usage(user_id=user.id, feature="suggest")
    q = await suggest_questions_for_user(user.id, limit=min(max(limit, 1), 10))
    return SuggestedQuestionsOut(questions=q)


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024


def _user_upload_dir(user_id: int) -> Path:
    path = UPLOAD_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_upload_dir(workspace_id: int) -> Path:
    path = UPLOAD_DIR / "workspaces" / str(workspace_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(raw: str | None) -> str:
    """Chỉ giữ tên file, bỏ mọi phần thư mục.

    Trước đây tên file từ client được ghép thẳng vào đường dẫn, nên tên như
    "../2/x.txt" có thể ghi đè file của người dùng khác (path traversal).
    """
    if not raw:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
    name = raw.replace("\\", "/").split("/")[-1].strip()
    if (
        not name
        or name in {".", ".."}
        or len(name) > 200
        or any(ord(c) < 32 for c in name)
    ):
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
    return name


def _path_in_user_dir(user_id: int, name: str) -> Path:
    user_dir = _user_upload_dir(user_id).resolve()
    dest = (user_dir / name).resolve()
    if dest.parent != user_dir:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
    return dest


def _path_in_workspace_dir(workspace_id: int, name: str) -> Path:
    workspace_dir = _workspace_upload_dir(workspace_id).resolve()
    dest = (workspace_dir / name).resolve()
    if dest.parent != workspace_dir:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")
    return dest


def _require_workspace_member(db: Session, workspace_id: int, user_id: int) -> None:
    member = (
        db.query(FieldWorkspaceMember)
        .filter(
            FieldWorkspaceMember.workspace_id == workspace_id,
            FieldWorkspaceMember.user_id == user_id,
        )
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="Không tìm thấy đội")


def _require_workspace_owner(db: Session, workspace_id: int, user_id: int) -> None:
    owner = (
        db.query(FieldWorkspaceMember)
        .filter(
            FieldWorkspaceMember.workspace_id == workspace_id,
            FieldWorkspaceMember.user_id == user_id,
            FieldWorkspaceMember.role == "owner",
        )
        .first()
    )
    if not owner:
        raise HTTPException(
            status_code=403, detail="Chỉ trưởng đội được cập nhật tài liệu dùng chung"
        )


@router.post("/upload", response_model=UploadResult)
async def upload_doc(
    file: UploadFile = File(...),
    workspace_id: int | None = Query(default=None, gt=0),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workspace_id is not None:
        _require_workspace_member(db, workspace_id, user.id)
        _require_workspace_owner(db, workspace_id, user.id)
    filename = safe_filename(file.filename)

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ hỗ trợ: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    dest = (
        _path_in_workspace_dir(workspace_id, filename)
        if workspace_id is not None
        else _path_in_user_dir(user.id, filename)
    )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File quá lớn (tối đa {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    if not content:
        raise HTTPException(status_code=400, detail="File rỗng")
    try:
        chunks = await ingest_file(user.id, dest, workspace_id=workspace_id, content=content)
    except Exception as exc:
        status = 429 if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc) else 500
        raise HTTPException(status_code=status, detail=format_ingest_error(exc)) from exc

    return UploadResult(
        filename=filename,
        chunks=chunks,
        message=f"{chunks} chunks đã được lập chỉ mục",
    )


@router.get("", response_model=list[DocItem])
async def list_docs(
    workspace_id: int | None = Query(default=None, gt=0),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workspace_id is not None:
        _require_workspace_member(db, workspace_id, user.id)
    docs = await list_user_docs(user.id, workspace_id=workspace_id)
    user_dir = (
        _workspace_upload_dir(workspace_id)
        if workspace_id is not None
        else _user_upload_dir(user.id)
    )
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


@router.get("/lineage", response_model=dict[str, LineageStatus | LineageItem])
async def documents_lineage(user: CurrentUser = Depends(get_current_user)):
    """Trạng thái Bronze/Silver/Gold thật của từng tài liệu — audit trail, không phải chỉ số bịa.

    Key "__status__" báo pipeline có bật/kết nối được Postgres hay không; các key còn lại
    là tên file với trạng thái lineage tương ứng.
    """
    return get_lineage_map(user.id)


@router.delete("/{filename}")
async def remove_doc(
    filename: str,
    workspace_id: int | None = Query(default=None, gt=0),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workspace_id is not None:
        _require_workspace_member(db, workspace_id, user.id)
        _require_workspace_owner(db, workspace_id, user.id)
    filename = safe_filename(filename)
    fpath = (
        _path_in_workspace_dir(workspace_id, filename)
        if workspace_id is not None
        else _path_in_user_dir(user.id, filename)
    )
    deleted = await delete_doc(user.id, filename, workspace_id=workspace_id, file_path=fpath)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")

    return {"message": f"Đã xóa {filename}", "deleted_chunks": deleted}
