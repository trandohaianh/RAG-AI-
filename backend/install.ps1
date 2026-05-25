# Cài backend trên Windows (workaround ChromaDB wheel)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) { python -m venv .venv }

.\.venv\Scripts\pip install --upgrade pip
.\.venv\Scripts\pip install "chroma-hnswlib==0.7.6a9"
.\.venv\Scripts\pip install "chromadb==0.6.3" --no-deps
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install bcrypt build grpcio importlib-resources kubernetes mmh3 onnxruntime opentelemetry-api opentelemetry-exporter-otlp-proto-grpc opentelemetry-instrumentation-fastapi opentelemetry-sdk overrides posthog pypika rich tokenizers typer

Write-Host "Xong. Đặt GEMINI_API_KEY trong ..\.env"
