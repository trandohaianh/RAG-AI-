# Grafana + Prometheus (optional monitoring — NOT RAG Chat UI)
# Login: admin / gig  ->  http://localhost:3002

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Starting Prometheus + Grafana (monitoring only)..." -ForegroundColor Cyan
# Docker Compose ghi status (vd. "Container ... Running") ra stderr — không phải lỗi thật
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker compose up -d --no-deps prometheus grafana 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = $prevEap
    Write-Host "docker compose failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
Write-Host "Reloading dashboards..." -ForegroundColor Gray
Start-Sleep -Seconds 2
docker compose restart grafana 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = $prevEap
    Write-Host "docker compose restart grafana failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
$ErrorActionPreference = $prevEap

Write-Host ""
Write-Host "=== Grafana (charts) ===" -ForegroundColor Green
Write-Host "  URL:       http://localhost:3002"
Write-Host "  Login:     admin / gig"
Write-Host "  Dashboard: Menu -> Dashboards -> folder 'RAG Chatbot' -> 'RAG Chatbot - DE Overview'"
Write-Host "  Or open:   http://localhost:3002/d/rag-de-overview"
Write-Host ""
Write-Host "  Check targets:  docker compose exec prometheus wget -qO- http://localhost:9090/api/v1/targets"
Write-Host ""
Write-Host "Backend API must run for data:  cd backend ; .\start.ps1"
Write-Host "Then use RAG app (chat):          .\start-web.ps1  -> http://localhost:3001"
Write-Host ""

Start-Sleep -Seconds 5
Start-Process "http://localhost:3002/d/rag-de-overview"
