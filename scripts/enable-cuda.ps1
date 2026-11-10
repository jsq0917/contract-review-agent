# 确保 .venv 里装的是 CUDA 版 torch（Windows 上目前只有 2.14.1+cu130 有轮子）。
#
# 为什么需要这个脚本：uv.lock 里 torch 是 2.15.0（PyPI CPU 版），而 Windows 没有
# 2.15.x 的 CUDA 轮子；任何一次 uv sync 都会把 CUDA 版换回 CPU 版。这里做幂等修复，
# 已可用 CUDA 时直接返回，不重复下载（轮子约 2 GB，靠 uv 缓存才不至于每次都等）。
#
# 失败不阻断启动：打印原因后返回，检索退回 CPU，/health 的 reranker_device 会如实显示 cpu。

param(
    [string]$Python = "",
    [string]$TorchVersion = "2.14.1+cu130",
    [string]$IndexUrl = "https://download.pytorch.org/whl/cu130"
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $Root ".venv\Scripts\python.exe" }

if (-not (Test-Path $Python)) {
    Write-Host "      未找到 $Python，跳过 CUDA torch 检查。" -ForegroundColor Yellow
    return
}

$probe = & $Python -c "import torch;print('CUDA' if torch.cuda.is_available() else 'CPU')" 2>$null
if ($LASTEXITCODE -eq 0 -and $probe -eq "CUDA") {
    $ver = & $Python -c "import torch;print(torch.__version__)" 2>$null
    $name = & $Python -c "import torch;print(torch.cuda.get_device_name(0))" 2>$null
    Write-Host "      torch $ver / $name，CUDA 可用，跳过安装。" -ForegroundColor Green
    return
}

Write-Host "      当前 torch 不可用 CUDA（probe=$probe），安装 torch $TorchVersion ..." -ForegroundColor Yellow
Write-Host "      首次需下载约 2 GB，之后走 uv 缓存。" -ForegroundColor DarkGray
try {
    uv pip install --python $Python `
        --index-url $IndexUrl `
        --extra-index-url https://pypi.org/simple `
        --index-strategy unsafe-best-match `
        "torch==$TorchVersion"
    if ($LASTEXITCODE -ne 0) { throw "uv pip install 退出码 $LASTEXITCODE" }
} catch {
    Write-Host "      安装失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "      继续以 CPU 运行；可单独重跑： powershell -ExecutionPolicy Bypass -File scripts\enable-cuda.ps1" -ForegroundColor Yellow
    return
}

$after = & $Python -c "import torch;print(torch.__version__, torch.cuda.is_available())" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "      完成：torch $after" -ForegroundColor Green
} else {
    Write-Host "      安装后校验失败，按 CPU 运行。" -ForegroundColor Yellow
}
