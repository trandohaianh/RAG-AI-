"""

Ingestion per-user: load → chunk → embed (rate-limited) → ChromaDB collection user_{id}.

"""

import asyncio

import hashlib

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





def _collection_name(user_id: int, workspace_id: int | None = None) -> str:
    if workspace_id is not None:
        return f"workspace_{workspace_id}"
    return f"user_{user_id}"





def _embeddings() -> GoogleGenerativeAIEmbeddings:

    return GoogleGenerativeAIEmbeddings(

        model=EMBEDDING_MODEL,

        google_api_key=GEMINI_API_KEY,

    )





def get_vectorstore(user_id: int, workspace_id: int | None = None):

    """Vectorstore LangChain — dùng cho similarity_search (query embed 1 lần)."""

    from langchain_chroma import Chroma



    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    return Chroma(

        collection_name=_collection_name(user_id, workspace_id),

        embedding_function=_embeddings(),

        persist_directory=str(CHROMA_DIR),

    )





def _load_sync(file_path: Path) -> list[Document]:

    suffix = file_path.suffix.lower()

    if suffix == ".pdf":

        loader = PyPDFLoader(str(file_path))

    elif suffix in (".docx", ".doc"):

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





def _store_sync(
    user_id: int,
    chunks: list[Document],
    source_name: str,
    workspace_id: int | None = None,
) -> int:

    if not chunks:

        return 0



    embedder = _embeddings()

    texts = [c.page_content for c in chunks]

    metadatas = [c.metadata for c in chunks]



    embeddings = embed_texts_with_retry(embedder, texts)



    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    collection = client.get_or_create_collection(
        name=_collection_name(user_id, workspace_id)
    )



    # Ghi đè chunk cùng tên file (upload lại)

    try:

        collection.delete(where={"source": source_name})

    except Exception:

        pass



    collection.add(

        ids=_chunk_ids(source_name, len(chunks)),

        embeddings=embeddings,

        documents=texts,

        metadatas=metadatas,

    )

    return len(chunks)





async def ingest_file(
    user_id: int,
    file_path: Path,
    dag_run_id: str | None = None,
    workspace_id: int | None = None,
) -> int:
    import time

    from metrics import EMBED_QUOTA_HITS, INGEST_DURATION, INGEST_ERRORS
    from pipeline.db import is_medallion_enabled
    from pipeline.medallion import run_medallion_for_file

    start = time.perf_counter()
    try:
        if is_medallion_enabled():
            result = await asyncio.to_thread(
                run_medallion_for_file, user_id, file_path, dag_run_id
            )
            if not result.get("skipped") and not result.get("success", True):
                INGEST_ERRORS.labels(reason="validation").inc()
                raise ValueError(
                    "Great Expectations validation thất bại — kiểm tra PostgreSQL silver layer"
                )

        documents = await asyncio.to_thread(_load_sync, file_path)
        chunks = await asyncio.to_thread(_split_sync, documents)
        count = await asyncio.to_thread(
            _store_sync, user_id, chunks, file_path.name, workspace_id
        )
        INGEST_DURATION.observe(time.perf_counter() - start)
        try:
            from rag.cache import invalidate_user_cache

            if workspace_id is not None:
                from rag.cache import invalidate_workspace_cache

                invalidate_workspace_cache(workspace_id)
            else:
                invalidate_user_cache(user_id)
        except Exception:
            pass
        return count
    except Exception as exc:
        if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
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

    except Exception:

        return []





async def delete_doc(
    user_id: int, filename: str, workspace_id: int | None = None
) -> int:

    return await asyncio.to_thread(_delete_doc_sync, user_id, filename, workspace_id)





def _delete_doc_sync(
    user_id: int, filename: str, workspace_id: int | None = None
) -> int:

    try:

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))

        collection = client.get_collection(_collection_name(user_id, workspace_id))

        before = collection.get(where={"source": filename}, include=[])

        count = len(before.get("ids") or [])

        if count > 0:

            collection.delete(where={"source": filename})

        try:

            from rag.cache import invalidate_user_cache

            if workspace_id is not None:
                from rag.cache import invalidate_workspace_cache

                invalidate_workspace_cache(workspace_id)
            else:
                invalidate_user_cache(user_id)

        except Exception:

            pass

        return count

    except Exception:

        return 0





def delete_user_collection(user_id: int) -> None:

    try:

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))

        client.delete_collection(_collection_name(user_id))

    except Exception:

        pass





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

