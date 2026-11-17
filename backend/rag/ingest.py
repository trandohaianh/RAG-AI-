"""

Ingestion per-user: load → chunk → embed (rate-limited) → ChromaDB collection user_{id}.

"""

import asyncio

import hashlib
import logging
import tempfile
import uuid

from pathlib import Path



import chromadb

from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader

from langchain_core.documents import Document

from langchain_google_genai import GoogleGenerativeAIEmbeddings

from langchain_google_genai._common import GoogleGenerativeAIError

from langchain_text_splitters import RecursiveCharacterTextSplitter



from config import (

    CHROMA_DIR,

    CHUNK_OVERLAP,

    CHUNK_SIZE,

    EMBEDDING_MODEL,

    GEMINI_API_KEY,

)

from rag.embed_utils import _is_quota_error, embed_texts_with_retry
from rag.document_lifecycle import (
    DocumentCommitUncertainError, DocumentRecoveryError, advance_revision, begin_transaction, collection_read_lock,
    commit_transaction, delete_raw, publication_lock, publish_raw,
    rollback_transaction, storage_lock, transaction_phase,
)

logger = logging.getLogger(__name__)





def _invalidate_bm25(collection_name: str) -> None:
    """Báo cho chỉ mục BM25 biết collection đã đổi. Import cục bộ để tránh vòng import.

    Chỉ mục BM25 nằm trong bộ nhớ nên không tự biết kho đã thay đổi. Thiếu bước này
    thì nó vẫn trả về tài liệu người dùng vừa gỡ bỏ.
    """
    try:
        from rag.bm25 import invalidate

        invalidate(collection_name)
    except Exception:
        pass


def _collection_name(user_id: int, workspace_id: int | None = None) -> str:
    if workspace_id is not None:
        return f"workspace_{workspace_id}"
    return f"user_{user_id}"





def _embeddings() -> GoogleGenerativeAIEmbeddings:
    """Embedding Gemini, có ghi token/chi phí (xem rag.embed_utils.MeteredEmbeddings)."""
    from rag.embed_utils import MeteredEmbeddings

    inner = GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GEMINI_API_KEY,
    )
    return MeteredEmbeddings(inner, EMBEDDING_MODEL)  # type: ignore[return-value]





def get_vectorstore(user_id: int, workspace_id: int | None = None):

    """Vectorstore LangChain — dùng cho similarity_search (query embed 1 lần)."""

    from langchain_chroma import Chroma



    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    return Chroma(

        collection_name=_collection_name(user_id, workspace_id),

        embedding_function=_embeddings(),

        persist_directory=str(CHROMA_DIR),

    )





# DOCX là zip: docx2txt giải nén cả word/document.xml vào RAM. XML lặp nén được ~1000:1, nên một
# file 20 MB có thể nở thành ~20 GB và giết tiến trình API (container giới hạn 2 GB).
DOCX_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
DOCX_MAX_RATIO = 100


class UnsafeDocumentError(ValueError):
    """Tài liệu bị từ chối trước khi đọc (zip bomb...). Thông điệp an toàn để trả cho người dùng."""


def _check_zip_bomb(file_path: Path) -> None:
    import zipfile

    if not zipfile.is_zipfile(file_path):
        return
    with zipfile.ZipFile(file_path) as archive:
        infos = archive.infolist()
    total = sum(info.file_size for info in infos)
    packed = sum(info.compress_size for info in infos)
    if total > DOCX_MAX_UNCOMPRESSED_BYTES or total > DOCX_MAX_RATIO * max(packed, 1):
        raise UnsafeDocumentError(
            "File DOCX giải nén quá lớn hoặc tỉ lệ nén bất thường — có thể là zip bomb, đã từ chối."
        )


def _check_extracted_size(file_path: Path, docs: list[Document]) -> None:
    from config import MAX_TEXT_CHARS_PER_BYTE

    limit = max(file_path.stat().st_size, 1) * MAX_TEXT_CHARS_PER_BYTE
    if sum(len(doc.page_content or "") for doc in docs) > limit:
        raise UnsafeDocumentError(
            "Văn bản trích ra dài bất thường so với kích thước file (nén quá mức) — đã từ chối."
        )


def _load_sync(file_path: Path) -> list[Document]:

    suffix = file_path.suffix.lower()

    if suffix == ".pdf":

        loader = PyPDFLoader(str(file_path))

    elif suffix in (".docx", ".doc"):

        _check_zip_bomb(file_path)
        loader = Docx2txtLoader(str(file_path))

    elif suffix == ".txt":

        loader = TextLoader(str(file_path), encoding="utf-8")

    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):

        from rag.ocr import extract_text_from_image_file

        text = extract_text_from_image_file(file_path)

        if not text.strip():

            raise ValueError("OCR không trích được chữ từ ảnh (thử ảnh rõ nét hơn)")

        return [

            Document(

                page_content=text,

                metadata={"source": file_path.name},

            )

        ]

    else:

        raise ValueError(f"Định dạng không hỗ trợ: {suffix}")



    docs = loader.load()

    _check_extracted_size(file_path, docs)

    for doc in docs:

        doc.metadata["source"] = file_path.name

        if "page" in doc.metadata and doc.metadata["page"] is not None:

            doc.metadata["page"] = int(doc.metadata["page"])

    return docs





def _split_sync(documents: list[Document]) -> list[Document]:

    splitter = RecursiveCharacterTextSplitter(

        chunk_size=CHUNK_SIZE,

        chunk_overlap=CHUNK_OVERLAP,

        length_function=len,

    )

    return splitter.split_documents(documents)





def _chunk_ids(source: str, count: int) -> list[str]:

    base = hashlib.md5(source.encode(), usedforsecurity=False).hexdigest()[:12]

    return [f"{base}_{i}" for i in range(count)]





def _invalidate_scope(user_id: int, workspace_id: int | None = None) -> None:
    _invalidate_bm25(_collection_name(user_id, workspace_id))
    try:
        from rag.cache import invalidate_user_cache, invalidate_workspace_cache

        if workspace_id is None:
            invalidate_user_cache(user_id)
        else:
            invalidate_workspace_cache(workspace_id)
    except Exception:
        logger.warning("Could not clear retrieval cache", exc_info=True)


def _store_sync(
    user_id: int, chunks: list[Document], source_name: str,
    workspace_id: int | None = None, *,
    staged_path: Path | None = None, publish_path: Path | None = None,
) -> int:
    """Prepare embeddings before publishing under document + collection locks.

    A persisted undo snapshot makes all noncommitted phases recoverable after
    process termination. Readers recover or fail closed before exposing chunks.
    """
    if not chunks:
        raise ValueError("Document contains no readable text")
    if publish_path is not None and staged_path is None:
        raise ValueError("Missing staged document")
    texts = [chunk.page_content for chunk in chunks]
    embeddings = embed_texts_with_retry(_embeddings(), texts)
    if len(embeddings) != len(texts):
        raise ValueError("Embedding count does not match document chunks")
    version = uuid.uuid4().hex
    ids = [f"{chunk_id}_{version}" for chunk_id in _chunk_ids(source_name, len(chunks))]
    metadatas = [
        {**chunk.metadata, "source": source_name, "document_version": version}
        for chunk in chunks
    ]
    collection_name = _collection_name(user_id, workspace_id)
    with publication_lock(CHROMA_DIR, collection_name, source_locked=source_name):
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_or_create_collection(name=collection_name)
        previous = collection.get(
            where={"source": source_name}, include=["documents", "metadatas", "embeddings"],
        )
        record = begin_transaction(
            CHROMA_DIR, collection_name, source_name, previous,
            new_ids=ids, raw_path=publish_path,
        )
        try:
            advance_revision(CHROMA_DIR, collection_name)
            transaction_phase(CHROMA_DIR, record, "adding")
            collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
            if publish_path is not None:
                transaction_phase(CHROMA_DIR, record, "publishing")
                publish_raw(staged_path, publish_path)
            if previous.get("ids"):
                transaction_phase(CHROMA_DIR, record, "retiring")
                collection.delete(ids=previous["ids"])
            commit_transaction(CHROMA_DIR, record)
        except DocumentCommitUncertainError:
            raise
        except Exception as original_error:
            try:
                rollback_transaction(CHROMA_DIR, record, collection)
            except DocumentRecoveryError as recovery_error:
                logger.critical("Document rollback needs recovery: %s", collection_name, exc_info=True)
                raise recovery_error from ExceptionGroup(
                    "Document update and rollback failed", [original_error, recovery_error]
                )
            raise
        finally:
            _invalidate_scope(user_id, workspace_id)
    return len(chunks)


def _ingest_prepared_sync(
    user_id: int, file_path: Path, dag_run_id: str | None,
    workspace_id: int | None, publish_path: Path | None = None,
) -> int:
    from pipeline.db import is_medallion_enabled
    from pipeline.medallion import run_medallion_for_file

    if is_medallion_enabled():
        result = run_medallion_for_file(user_id, file_path, dag_run_id)
        if not result.get("skipped") and not result.get("success", True):
            raise ValueError("Document validation failed")
    documents = _load_sync(file_path)
    chunks = _split_sync(documents)
    return _store_sync(
        user_id, chunks, file_path.name, workspace_id,
        staged_path=file_path if publish_path is not None else None,
        publish_path=publish_path,
    )


def _ingest_file_sync(
    user_id: int, file_path: Path, dag_run_id: str | None = None,
    workspace_id: int | None = None, content: bytes | None = None,
) -> int:
    collection_name = _collection_name(user_id, workspace_id)
    with storage_lock(CHROMA_DIR, collection_name, source=file_path.name):
        # Recover before reading an existing raw file for a batch reindex.
        with publication_lock(CHROMA_DIR, collection_name, source_locked=file_path.name):
            pass
        if content is None:
            return _ingest_prepared_sync(user_id, file_path, dag_run_id, workspace_id)
        # The worker owns staging cleanup, including when the HTTP task is cancelled.
        with tempfile.TemporaryDirectory(
            prefix=".document-stage-", dir=file_path.parent, ignore_cleanup_errors=True
        ) as staging:
            staged = Path(staging) / file_path.name
            staged.write_bytes(content)
            return _ingest_prepared_sync(user_id, staged, dag_run_id, workspace_id, file_path)


async def ingest_file(
    user_id: int,
    file_path: Path,
    dag_run_id: str | None = None,
    workspace_id: int | None = None,
    *,
    content: bytes | None = None,
) -> int:
    import time

    from metrics import EMBED_QUOTA_HITS, INGEST_DURATION, INGEST_ERRORS

    from costs.context import usage_scope

    start = time.perf_counter()
    try:
        # to_thread sao chép context → token embedding được gán đúng user/workspace.
        with usage_scope(user_id=user_id, workspace_id=workspace_id):
            count = await asyncio.to_thread(
                _ingest_file_sync, user_id, file_path, dag_run_id, workspace_id, content
            )
        INGEST_DURATION.observe(time.perf_counter() - start)
        return count
    except Exception as exc:
        if _is_quota_error(exc):
            EMBED_QUOTA_HITS.inc()
            INGEST_ERRORS.labels(reason="quota").inc()
        else:
            INGEST_ERRORS.labels(reason="other").inc()
        raise


async def list_user_docs(
    user_id: int, workspace_id: int | None = None
) -> list[dict]:

    return await asyncio.to_thread(_list_docs_sync, user_id, workspace_id)





def _list_docs_sync(user_id: int, workspace_id: int | None = None) -> list[dict]:

    try:

        with collection_read_lock(CHROMA_DIR, _collection_name(user_id, workspace_id)):
            client = chromadb.PersistentClient(path=str(CHROMA_DIR))

            collection = client.get_collection(_collection_name(user_id, workspace_id))

            result = collection.get(include=["metadatas"])

        counts: dict[str, int] = {}

        for meta in result.get("metadatas") or []:

            if meta and meta.get("source"):

                name = meta["source"]

                counts[name] = counts.get(name, 0) + 1

        return [

            {"name": name, "chunks": count}

            for name, count in sorted(counts.items())

        ]

    except DocumentRecoveryError:
        raise
    except Exception:

        return []





async def delete_doc(
    user_id: int, filename: str, workspace_id: int | None = None,
    *, file_path: Path | None = None,
) -> int:
    return await asyncio.to_thread(_delete_doc_sync, user_id, filename, workspace_id, file_path)


def _delete_doc_sync(
    user_id: int, filename: str, workspace_id: int | None = None,
    file_path: Path | None = None,
) -> int:
    collection_name = _collection_name(user_id, workspace_id)
    with storage_lock(CHROMA_DIR, collection_name, source=filename):
        with publication_lock(CHROMA_DIR, collection_name, source_locked=filename):
            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            try:
                collection = client.get_collection(collection_name)
            except chromadb.errors.NotFoundError:
                return 0
            previous = collection.get(
                where={"source": filename}, include=["documents", "metadatas", "embeddings"]
            )
            ids = previous.get("ids") or []
            if not ids:
                return 0
            record = begin_transaction(
                CHROMA_DIR, collection_name, filename, previous,
                new_ids=[], raw_path=file_path, operation="delete",
            )
            try:
                advance_revision(CHROMA_DIR, collection_name)
                transaction_phase(CHROMA_DIR, record, "deleting")
                collection.delete(ids=ids)
                if file_path is not None:
                    delete_raw(file_path)
                commit_transaction(CHROMA_DIR, record)
            except DocumentCommitUncertainError:
                raise
            except Exception as original_error:
                try:
                    rollback_transaction(CHROMA_DIR, record, collection)
                except DocumentRecoveryError as recovery_error:
                    raise recovery_error from ExceptionGroup(
                        "Document deletion and rollback failed", [original_error, recovery_error]
                    )
                raise
            finally:
                _invalidate_scope(user_id, workspace_id)
            return len(ids)


def delete_user_collection(user_id: int) -> None:
    collection_name = _collection_name(user_id)
    with publication_lock(CHROMA_DIR, collection_name):
        advance_revision(CHROMA_DIR, collection_name)
        try:
            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            client.delete_collection(collection_name)
        except chromadb.errors.NotFoundError:
            pass
        finally:
            _invalidate_scope(user_id)


def format_ingest_error(exc: BaseException) -> str:

    if _is_quota_error(exc):

        return (

            "Đã vượt giới hạn embedding miễn phí của Google (~100 lần/phút). "

            "Đợi khoảng 1 phút rồi upload lại, hoặc thử file nhỏ hơn / ít trang hơn. "

            "Xem: https://ai.google.dev/gemini-api/docs/rate-limits"

        )

    if isinstance(exc, GoogleGenerativeAIError):

        return str(exc).replace("Error embedding content", "Lỗi embedding").split("\n")[0][:500]

    return str(exc)[:500]

