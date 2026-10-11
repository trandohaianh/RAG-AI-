# Start backend API (run before frontend)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Port = if ($env:PORT) { $env:PORT } else { 8000 }

if (-not (Test-Path ".venv\Scripts\uvicorn.exe")) {
    Write-Host "Missing .venv. Run: .\install.ps1" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "Missing .env. Copy from .env.example and set GEMINI_API_KEY + SECRET_KEY" -ForegroundColor Red
    exit 1
}

$healthUrl = "http://127.0.0.1:$Port/health"
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    try {
        $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        if ($h.status -eq "ok") {
            Write-Host "Backend already running: $healthUrl" -ForegroundColor Green
            Write-Host "Swagger: http://127.0.0.1:$Port/docs" -ForegroundColor Green
            exit 0
        }
    } catch { }
    $procId = $inUse[0].OwningProcess
    Write-Host "Port $Port in use (PID $procId) but API not healthy." -ForegroundColor Yellow
    Write-Host "  Kill: taskkill /PID $procId /F" -ForegroundColor Yellow
    Write-Host "  Or:   `$env:PORT=8001; .\start.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "Backend: http://127.0.0.1:$Port (health: /health)" -ForegroundColor Green
Write-Host "Swagger: http://127.0.0.1:$Port/docs" -ForegroundColor Green
Write-Host "LAN/Tailscale: dung 'ipconfig' hoac 'tailscale ip' de lay dia chi, vi du http://100.x.x.x:$Port" -ForegroundColor Green
.\.venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port $Port --reload
