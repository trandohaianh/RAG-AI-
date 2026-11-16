"""Prometheus metrics — latency, errors, RAG retrieval."""
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

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


HTTP_DURATION = Histogram(
    "rag_http_request_duration_seconds",
    "Thời gian xử lý HTTP theo route template (giây)",
    ["method", "route"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

# --- Chi phí AI (nhãn ít giá trị để không vượt giới hạn series của Grafana Cloud free) ---
LLM_REQUESTS = Counter(
    "fieldops_llm_requests_total",
    "Số lần gọi model",
    ["provider", "model", "feature", "status"],
)
LLM_TOKENS = Counter(
    "fieldops_llm_tokens_total",
    "Số token đã dùng",
    ["provider", "model", "feature", "kind"],
)
LLM_COST_USD = Counter(
    "fieldops_llm_cost_usd_total",
    "Chi phí AI quy đổi theo giá niêm yết (USD)",
    ["provider", "model", "feature"],
)
LLM_LATENCY = Histogram(
    "fieldops_llm_latency_seconds",
    "Độ trễ một lần gọi model (giây)",
    ["provider", "feature"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 60),
)
INFRA_COST_MONTH_USD = Gauge(
    "fieldops_infra_cost_month_usd",
    "Tổng chi phí hạ tầng tháng hiện tại (USD, nhập tay)",
)

FIELD_WEBHOOKS = Counter(
    "fieldops_field_webhooks_total",
    "Webhook CRM/NMS: vào (accepted/duplicate/rejected) và ra (delivered/retry/dead)",
    ["direction", "outcome"],
)
FIELD_PHOTOS = Counter("fieldops_field_photos_total", "Ảnh hiện trường", ["outcome"])

AUTH_EVENTS = Counter(
    "fieldops_auth_events_total",
    "Sự kiện xác thực",
    ["event", "result"],
)


def metrics_response():
    return generate_latest(), CONTENT_TYPE_LATEST


class _PipelineCollector:
    """Đọc trạng thái pipeline từ DB mỗi lần scrape (60s/lần với Grafana Cloud → rất nhẹ)."""

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily

        last_success = GaugeMetricFamily(
            "fieldops_pipeline_last_success_timestamp_seconds",
            "Thời điểm job pipeline chạy thành công gần nhất (unix)", labels=["task"])
        last_status = GaugeMetricFamily(
            "fieldops_pipeline_last_status",
            "Trạng thái lần chạy gần nhất: 1=ok, 0.5=warn, 0=fail", labels=["task"])
        quality = GaugeMetricFamily(
            "fieldops_data_quality_violations",
            "Số vi phạm của từng check chất lượng dữ liệu (lần chạy gần nhất)", labels=["check", "severity"])
        # Nhãn "task" thay vì "job": "job" bị Prometheus/Grafana Cloud ghi đè bằng tên scrape job.
        try:
            import json

            from database import SessionLocal
            from pipeline.jobs.models import PipelineRun
            from pipeline.jobs.runner import DAILY_CHAIN

            with SessionLocal() as db:
                for job in DAILY_CHAIN:
                    last = (db.query(PipelineRun).filter(PipelineRun.job == job, PipelineRun.status != "running")
                            .order_by(PipelineRun.id.desc()).first())
                    if last is None:
                        continue
                    last_status.add_metric([job], {"ok": 1.0, "warn": 0.5}.get(last.status, 0.0))
                    ok = (db.query(PipelineRun).filter(PipelineRun.job == job, PipelineRun.status.in_(("ok", "warn")))
                          .order_by(PipelineRun.id.desc()).first())
                    if ok and ok.finished_at:
                        finished = ok.finished_at
                        if finished.tzinfo is None:  # SQLite trả datetime không múi giờ (đã lưu UTC)
                            from datetime import timezone

                            finished = finished.replace(tzinfo=timezone.utc)
                        last_success.add_metric([job], finished.timestamp())
                    if job == "data_quality" and last.details_json:
                        for name, item in (json.loads(last.details_json).get("checks") or {}).items():
                            quality.add_metric([name, item.get("severity", "warn")], float(item.get("violations", 0)))
        except Exception:
            pass  # DB chưa sẵn sàng: bỏ qua, không làm hỏng /metrics
        yield last_success
        yield last_status
        yield quality

        # Dung lượng ổ dữ liệu: VM free có 200GB tổng, cần cảnh báo trước khi đầy.
        disk = GaugeMetricFamily("fieldops_data_disk_bytes", "Dung lượng ổ chứa DATA_DIR", labels=["kind"])
        try:
            import shutil

            from config import DATA_ROOT

            usage = shutil.disk_usage(DATA_ROOT)
            disk.add_metric(["total"], float(usage.total))
            disk.add_metric(["free"], float(usage.free))
        except Exception:
            pass
        yield disk


def _register_pipeline_collector():
    from prometheus_client import REGISTRY

    try:
        REGISTRY.register(_PipelineCollector())
    except ValueError:
        pass


_register_pipeline_collector()
