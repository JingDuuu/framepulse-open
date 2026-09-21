$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { throw '需要 Python 3.10 或更高版本，请先安装 Python 并勾选 Add Python to PATH。' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $pythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败' }
}
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw '更新 pip 失败' }
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败，请检查网络后重试' }
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { throw '源码桌面版还需要 Node.js 22.12 或更高版本。请安装后再次运行 Setup，或直接使用已打包的 FramePulse 安装版。' }
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) { throw '未找到 node.exe，请安装 Node.js 22.12 或更高版本。' }
$nodeVersion = (& $nodeCommand.Source --version).TrimStart('v')
if ([version]$nodeVersion -lt [version]'22.12.0') { throw '当前 Node.js 版本过旧，源码桌面版需要 22.12 或更高版本。安装版不需要 Node.js。' }
Push-Location (Join-Path $projectRoot 'desktop')
try {
    & $npmCommand.Source ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw '桌面运行环境安装失败，请检查网络后重试' }
} finally { Pop-Location }
Write-Host '安装完成。双击 Start-FramePulse.cmd 启动。' -ForegroundColor Green
