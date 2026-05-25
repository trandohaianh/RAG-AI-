@echo off
REM Double-click — chay web micro (embed chat)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-micro.ps1"
pause
