"""Embedding với retry khi hết quota Gemini (429)."""

import re
import time

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_google_genai._common import GoogleGenerativeAIError

from config import EMBED_BATCH_DELAY_SEC, EMBED_BATCH_SIZE, EMBED_MAX_RETRIES


def _parse_retry_seconds(message: str) -> float | None:
    m = re.search(r"retry in ([\d.]+)s", message, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r'"retryDelay":\s*"(\d+)s"', message)
    if m:
        return float(m.group(1))
    return None


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc).upper()
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "QUOTA" in text


def embed_texts_with_retry(
    embedder: GoogleGenerativeAIEmbeddings,
    texts: list[str],
) -> list[list[float]]:
    """Embed theo batch nhỏ, có delay + retry khi 429."""
    if not texts:
        return []

    all_embeddings: list[list[float]] = []
    batch_size = max(1, min(EMBED_BATCH_SIZE, 100))

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        last_err: BaseException | None = None

        for attempt in range(EMBED_MAX_RETRIES):
            try:
                vectors = embedder.embed_documents(batch, batch_size=len(batch))
                all_embeddings.extend(vectors)
                last_err = None
                break
            except (GoogleGenerativeAIError, Exception) as exc:
                last_err = exc
                if not _is_quota_error(exc) or attempt >= EMBED_MAX_RETRIES - 1:
                    raise
                wait = _parse_retry_seconds(str(exc)) or 55.0
                time.sleep(min(wait + 2.0, 120.0))

        if last_err is not None:
            raise last_err

        if start + batch_size < len(texts) and EMBED_BATCH_DELAY_SEC > 0:
            time.sleep(EMBED_BATCH_DELAY_SEC)

    return all_embeddings


class MeteredEmbeddings:
    """Bọc một LangChain Embeddings để ghi số token (ước lượng) và chi phí.

    Gemini embedding API không trả usage qua LangChain nên token được ước lượng
    (≈ 4 ký tự/token) và đánh dấu `estimated`. Chỉ ghi khi gọi thành công.
    """

    def __init__(self, inner, model: str):
        self._inner = inner
        self._model = model

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _record(self, texts: list[str], feature: str, start: float) -> None:
        from costs.recorder import estimate_tokens, record_usage

        record_usage(
            provider="gemini",
            model=self._model,
            input_tokens=sum(estimate_tokens(t) for t in texts),
            estimated=True,
            latency_ms=int((time.perf_counter() - start) * 1000),
            feature=feature,
        )

    def embed_documents(self, texts: list[str], *args, **kwargs) -> list[list[float]]:
        start = time.perf_counter()
        vectors = self._inner.embed_documents(texts, *args, **kwargs)
        self._record(list(texts), "embed_ingest", start)
        return vectors

    def embed_query(self, text: str, *args, **kwargs) -> list[float]:
        start = time.perf_counter()
        vector = self._inner.embed_query(text, *args, **kwargs)
        self._record([text], "embed_query", start)
        return vector

    async def aembed_documents(self, texts: list[str], *args, **kwargs):
        start = time.perf_counter()
        vectors = await self._inner.aembed_documents(texts, *args, **kwargs)
        self._record(list(texts), "embed_ingest", start)
        return vectors

    async def aembed_query(self, text: str, *args, **kwargs):
        start = time.perf_counter()
        vector = await self._inner.aembed_query(text, *args, **kwargs)
        self._record([text], "embed_query", start)
        return vector
