@echo off
chcp 65001 >nul
setlocal
echo Stopping U-Ting service ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'D:\Ucampus\.venv\*' } | ForEach-Object { Write-Host ('  kill PID ' + $_.Id); Stop-Process -Id $_.Id -Force }"
ping -n 3 127.0.0.1 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { (Invoke-WebRequest 'http://127.0.0.1:8710/api/health' -UseBasicParsing -TimeoutSec 2) | Out-Null; Write-Host '  still running (port busy?)' } catch { Write-Host '  stopped.' }"
echo Done.
ping -n 3 127.0.0.1 >nul
