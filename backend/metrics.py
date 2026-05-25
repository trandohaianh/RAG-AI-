"""Prometheus metrics — latency, errors, RAG retrieval."""
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

HTTP_REQUESTS = Counter(
    "rag_http_requests_total",
    "Tổng HTTP requests",
    ["method", "endpoint", "status"],
)

CHAT_LATENCY = Histogram(
    "rag_chat_duration_seconds",
    "Thời gian xử lý chat RAG (giây)",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60),
)

INGEST_DURATION = Histogram(
    "rag_ingest_duration_seconds",
    "Thời gian ingest tài liệu (giây)",
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

CHUNKS_RETRIEVED = Histogram(
    "rag_chunks_retrieved",
    "Số chunk retrieve mỗi câu hỏi",
    buckets=(0, 1, 2, 3, 5, 10),
)

INGEST_ERRORS = Counter(
    "rag_ingest_errors_total",
    "Lỗi ingest (validation / embedding / API)",
    ["reason"],
)

EMBED_QUOTA_HITS = Counter(
    "rag_embed_quota_exceeded_total",
    "Số lần vượt quota embedding Gemini",
)


def metrics_response():
    return generate_latest(), CONTENT_TYPE_LATEST
