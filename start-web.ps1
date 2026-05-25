# RAG Chat - Windows launcher (2 terminals + browser)
# Usage: cd d:\self   then   .\start-web.ps1
# Web micro (embed):  .\start-micro.ps1  hoặc  .\start-web.ps1 -Micro

param(
    [switch]$Micro
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$BackendPort = 8000
if ($env:PORT) { $BackendPort = [int]$env:PORT }
$FrontendPort = 0
if ($env:WEB_PORT) { $FrontendPort = [int]$env:WEB_PORT }

function Test-PortListen([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $c
}

function Test-Health([string]$url) {
    try {
        $r = Invoke-RestMethod -Uri $url -TimeoutSec 3
        return $r.status -eq "ok"
    } catch {
        return $false
    }
}

function Test-Grafana([int]$port) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$port" -UseBasicParsing -TimeoutSec 1
        return $r.Content -like "*Grafana*"
    } catch {
        return $false
    }
}

function Test-WebReady([string]$url) {
    try {
        $wr = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
        return ($wr.StatusCode -eq 200) -and ($wr.Content -like "*RAG*")
    } catch {
        return $false
    }
}

function Get-WebPort {
    if ($FrontendPort -gt 0) { return $FrontendPort }
    if (-not (Test-PortListen 3000)) { return 3000 }
    if (Test-Grafana 3000) {
        Write-Host "Port 3000 = Grafana (Docker). RAG web uses 3001." -ForegroundColor Yellow
        return 3001
    }
    if (-not (Test-PortListen 3001)) { return 3001 }
    return 3002
}

if (-not (Test-Path "$Root\backend\.venv\Scripts\uvicorn.exe")) {
    Write-Host "Run: cd backend ; .\install.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path "$Root\frontend\node_modules")) {
    Write-Host "Run: cd frontend ; npm install" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path "$Root\.env") -and -not (Test-Path "$Root\backend\.env")) {
    Write-Host "Missing .env - copy .env.example and set GEMINI_API_KEY" -ForegroundColor Red
    exit 1
}

$WebPort = Get-WebPort
$WebUrl = "http://localhost:$WebPort"
$ApiUrl = "http://127.0.0.1:$BackendPort"

# Port occupied but not our Next app (zombie / wrong process) -> pick free port
if ((Test-PortListen $WebPort) -and -not (Test-WebReady $WebUrl)) {
    Write-Host "Port $WebPort busy but RAG web not responding. Finding free port..." -ForegroundColor Yellow
    foreach ($p in @(3001, 3002, 3003, 3004, 3000)) {
        $tryUrl = "http://localhost:$p"
        if (Test-WebReady $tryUrl) {
            $WebPort = $p
            $WebUrl = $tryUrl
            break
        }
        if (-not (Test-PortListen $p)) {
            $WebPort = $p
            $WebUrl = $tryUrl
            break
        }
    }
}

$OpenLabel = if ($Micro) { "Web micro (embed)" } else { "Web app" }
Write-Host ""
Write-Host "=== RAG Chat ===" -ForegroundColor Cyan
Write-Host "  $OpenLabel : $WebUrl"
Write-Host "  API: $ApiUrl/docs"
Write-Host ""

if (Test-Health "$ApiUrl/health") {
    Write-Host "[Backend] Already running" -ForegroundColor Green
} elseif (Test-PortListen $BackendPort) {
    Write-Host "[Backend] Port $BackendPort busy - check old process" -ForegroundColor Yellow
} else {
    Write-Host "[Backend] Starting new window..." -ForegroundColor Green
    $cors = "http://localhost:$WebPort,http://127.0.0.1:$WebPort"
    $bCmd = "Set-Location '$Root\backend'; `$env:CORS_ORIGINS='$cors'; Write-Host 'RAG API http://127.0.0.1:$BackendPort (CORS: $cors)' -ForegroundColor Green; .\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port $BackendPort --reload"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $bCmd
}

$webRunning = Test-WebReady $WebUrl

if ($webRunning) {
    Write-Host "[Frontend] Already running" -ForegroundColor Green
} elseif (Test-PortListen $WebPort) {
    Write-Host "[Frontend] Port $WebPort busy - try: `$env:WEB_PORT=3002; .\start-web.ps1" -ForegroundColor Yellow
} else {
    Write-Host "[Frontend] Starting new window on port $WebPort..." -ForegroundColor Green
    $fCmd = "Set-Location '$Root\frontend'; Write-Host 'RAG Web $WebUrl' -ForegroundColor Green; npm run dev -- -p $WebPort"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $fCmd
}

Write-Host "Waiting for servers (max ~20s)..." -ForegroundColor Gray
$apiOk = $false
$webOk = $webRunning
for ($i = 0; $i -lt 40; $i++) {
    if (-not $apiOk) { $apiOk = Test-Health "$ApiUrl/health" }
    if (-not $webOk) { $webOk = Test-WebReady $WebUrl }
    if ($apiOk -and $webOk) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $apiOk) {
    Write-Host "[Backend] Chua san sang — van mo trinh duyet; kiem tra http://127.0.0.1:$BackendPort/health" -ForegroundColor Yellow
}
if (-not $webOk) {
    Write-Host "[Frontend] Dang compile lan dau (Next.js) — F5 sau 10-20 giay neu trang trong" -ForegroundColor Yellow
}

$openUrl = $WebUrl
if ($Micro) {
    $py = "$Root\backend\.venv\Scripts\python.exe"
    $token = & $py "$Root\backend\scripts\ensure_dev_embed_token.py" 2>&1 | Out-String
    $token = $token.Trim()
    if ($LASTEXITCODE -ne 0 -or -not $token) {
        Write-Host "[Micro] Chua co embed token / user. Mo trang dang ky: $WebUrl/register" -ForegroundColor Yellow
        $openUrl = "$WebUrl/register"
    } else {
        $apiQ = [uri]::EscapeDataString($ApiUrl)
        $tokQ = [uri]::EscapeDataString($token)
        $openUrl = "$WebUrl/embed?token=$tokQ&api=$apiQ"
        Write-Host "[Micro] Embed: $openUrl" -ForegroundColor Green
    }
} else {
    $openUrl = "$WebUrl/chat"
}

Start-Process $openUrl
Write-Host "Browser opened: $openUrl" -ForegroundColor Green
Write-Host "Stop: close Backend + Frontend PowerShell windows" -ForegroundColor Gray
Write-Host ""
