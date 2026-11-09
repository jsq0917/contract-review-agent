# 一键启动 M0：安装依赖 -> 启动 FastAPI -> 启动 Streamlit
# 用法：powershell -ExecutionPolicy Bypass -File scripts/dev.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "[1/3] uv sync ..." -ForegroundColor Cyan
uv sync

Write-Host "[2/3] 启动 FastAPI: http://127.0.0.1:8000/docs" -ForegroundColor Cyan
Start-Process -FilePath "uv" -ArgumentList "run", "lexflow-api" -WorkingDirectory $Root

Write-Host "[3/3] 启动 Streamlit: http://127.0.0.1:8501" -ForegroundColor Cyan
Start-Process -FilePath "uv" -ArgumentList "run", "streamlit", "run", "lexflow/ui/app.py" -WorkingDirectory $Root

Write-Host "已在新窗口启动；关闭对应窗口即可停止服务。" -ForegroundColor Green
