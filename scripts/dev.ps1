# 一键启动 M0：同步依赖 -> 启动 FastAPI -> 启动 Streamlit
# 用法：powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
# 可选：powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -Extras local-embed,ocr
#       （默认带 local-embed，否则向量检索会退化为 BM25-only）
#
# 注意 1：本文件必须保存为 UTF-8 with BOM，否则 Windows PowerShell 5.1 会按 GBK 解码，报「字符串缺少终止符」。
# 注意 2：启动 API 必须用 python -m lexflow.api.main，不能用 uv run lexflow-api：
#         pyproject 里 tool.uv.package = false，项目自身入口点不会被安装，lexflow-api 命令并不存在。

param(
    [string[]]$Extras = @('local-embed'),
    [switch]$NoCuda
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "[1/5] uv sync（extras: $($Extras -join ', ')）..." -ForegroundColor Cyan
$syncArgs = @('sync')
foreach ($extra in $Extras) {
    if ($extra) { $syncArgs += @('--extra', $extra) }
}
# 已有可用 CUDA torch 时跳过 torch：否则每次启动都会被 lock 里的 2.15.0（CPU 版）换掉，
# 下一步再装回 2.14.1+cu130，白等一次 2 GB 的解包。
# --inexact 不能省：只用 --no-install-package 时，uv 会把 lock 之外的 2.14.1+cu130
# 当成多余包直接卸载（已用 --dry-run 实测过）。
if (-not $NoCuda -and (Test-Path $VenvPy)) {
    $cudaReady = & $VenvPy -c "import torch;print(torch.cuda.is_available())" 2>$null
    if ($cudaReady -eq "True") { $syncArgs += @('--inexact', '--no-install-package', 'torch') }
}
uv @syncArgs

Write-Host "[2/5] 检查 CUDA torch ..." -ForegroundColor Cyan
if ($NoCuda) {
    Write-Host "      -NoCuda：跳过，保持当前 torch。" -ForegroundColor Yellow
} else {
    & "$PSScriptRoot\enable-cuda.ps1" -Python $VenvPy
}

function Test-HttpOk([string]$Uri) {
    try {
        $resp = Invoke-WebRequest -Uri $Uri -TimeoutSec 3 -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

Write-Host "[3/5] 检查端口 ..." -ForegroundColor Cyan
# 端口被占时先判断是不是本项目的服务：健康就直接复用，避免再拉起一个必然绑定失败的进程；
# 被其他程序占用则直接退出，不盲目抢端口。
$ApiRunning = $false
$UiRunning = $false
foreach ($port in 8000, 8501) {
    $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $busy) { continue }
    $ownerText = ($busy | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ', '
    if ($port -eq 8000 -and (Test-HttpOk "http://127.0.0.1:8000/health")) {
        Write-Host "      8000 已有 API 在运行（PID: $ownerText），直接复用，不再启动第二个进程。" -ForegroundColor Yellow
        $ApiRunning = $true
        continue
    }
    if ($port -eq 8501 -and (Test-HttpOk "http://127.0.0.1:8501")) {
        Write-Host "      8501 已有 Streamlit 在运行（PID: $ownerText），直接复用，不再启动第二个进程。" -ForegroundColor Yellow
        $UiRunning = $true
        continue
    }
    Write-Host "端口 $port 被其他程序占用（PID: $ownerText）且服务不可用，请先释放端口后重试。" -ForegroundColor Red
    exit 1
}

if ($ApiRunning) {
    Write-Host "[4/5] FastAPI 已在运行，跳过启动。" -ForegroundColor Cyan
} else {
    Write-Host "[4/5] 启动 FastAPI: http://127.0.0.1:8000/docs" -ForegroundColor Cyan
    # 用 venv 的 python 直接起，不走 uv run：uv run 会隐式 sync，把 CUDA torch 换回 CPU 版。
    Start-Process -FilePath $VenvPy -ArgumentList "-m", "lexflow.api.main" -WorkingDirectory $Root

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -UseBasicParsing
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if ($ready) {
        Write-Host "      API 已就绪。" -ForegroundColor Green
    } else {
        Write-Host "      API 20 秒内未就绪，请查看新打开的 FastAPI 窗口报错。" -ForegroundColor Yellow
    }
}

if ($UiRunning) {
    Write-Host "[5/5] Streamlit 已在运行，跳过启动：http://127.0.0.1:8501" -ForegroundColor Cyan
} else {
    Write-Host "[5/5] 启动 Streamlit: http://127.0.0.1:8501" -ForegroundColor Cyan
    # app.py 用 LEXFLOW_API_URL 找后端；子进程继承当前进程的环境变量。
    $env:LEXFLOW_API_URL = "http://127.0.0.1:8000"
    Start-Process -FilePath $VenvPy -ArgumentList "-m", "streamlit", "run", "lexflow/ui/app.py", "--server.port", "8501" -WorkingDirectory $Root
}

if ($ApiRunning -and $UiRunning) {
    Write-Host "两个服务都已在运行，本次未启动新进程。" -ForegroundColor Green
} else {
    Write-Host "已在新窗口启动；关闭对应窗口即可停止服务。" -ForegroundColor Green
}
