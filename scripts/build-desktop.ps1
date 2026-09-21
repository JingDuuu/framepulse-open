param(
    [switch]$BackendOnly,
    [switch]$SkipBackend,
    [switch]$Unpacked,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$desktopRoot = Join-Path $workspaceRoot 'desktop'
$buildPython = Join-Path $workspaceRoot '.qa-venv\Scripts\python.exe'
$backendDist = Join-Path $workspaceRoot 'build\backend'
$backendWork = Join-Path $workspaceRoot 'build\pyinstaller'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}

if (-not (Test-Path -LiteralPath $buildPython)) {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
    Invoke-Checked $pythonCommand.Source @('-m', 'venv', (Join-Path $workspaceRoot '.qa-venv'))
}

$venvConfig = Get-Content -LiteralPath (Join-Path $workspaceRoot '.qa-venv\pyvenv.cfg') -Raw
if ($venvConfig -notmatch '(?im)^include-system-site-packages\s*=\s*false\s*$') {
    throw '.qa-venv must be isolated (include-system-site-packages = false).'
}

Push-Location $workspaceRoot
try {
    if (-not $SkipBackend) {
        if (-not $SkipInstall) {
            Invoke-Checked $buildPython @('-m', 'pip', 'install', '--disable-pip-version-check', '.', 'pyinstaller==6.22.3')
        }
        Invoke-Checked $buildPython @((Join-Path $desktopRoot 'create-icon.py'))
        Invoke-Checked $buildPython @((Join-Path $desktopRoot 'collect-notices.py'))
        # Rebuild the current source tree, never a previously installed wheel.
        $previousPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = Join-Path $workspaceRoot 'src'
            Invoke-Checked $buildPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', $backendDist,
                '--workpath', $backendWork, (Join-Path $desktopRoot 'backend.spec'))
        } finally { $env:PYTHONPATH = $previousPythonPath }
        $frozenBackend = Join-Path $backendDist 'framepulse-backend\framepulse-backend.exe'
        Invoke-Checked $frozenBackend @('--version')
        Invoke-Checked $frozenBackend @('capabilities')
    }
    if ($BackendOnly) { return }
    $backendBinary = Join-Path $backendDist 'framepulse-backend\framepulse-backend.exe'
    if (-not (Test-Path -LiteralPath $backendBinary)) { throw 'No frozen backend found; run the backend build first.' }
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop
    Set-Location $desktopRoot
    if (-not $SkipInstall) {
        if (Test-Path -LiteralPath (Join-Path $desktopRoot 'package-lock.json')) {
            Invoke-Checked $npmCommand.Source @('ci', '--no-audit', '--no-fund')
        } else {
            Invoke-Checked $npmCommand.Source @('install', '--no-audit', '--no-fund')
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot 'node_modules\electron\dist\electron.exe'))) {
        $nodeCommand = Get-Command node.exe -ErrorAction Stop
        Invoke-Checked $nodeCommand.Source @((Join-Path $desktopRoot 'node_modules\electron\install.js'))
    }
    Invoke-Checked $npmCommand.Source @('test')
    $oldSigningDiscovery = $env:CSC_IDENTITY_AUTO_DISCOVERY
    try {
        $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
        if ($Unpacked) { Invoke-Checked $npmCommand.Source @('run', 'pack') }
        else { Invoke-Checked $npmCommand.Source @('run', 'dist') }
    } finally { $env:CSC_IDENTITY_AUTO_DISCOVERY = $oldSigningDiscovery }
    if (-not $Unpacked) {
        $nodeCommand = Get-Command node.exe -ErrorAction Stop
        Invoke-Checked $nodeCommand.Source @((Join-Path $desktopRoot 'write-manifest.cjs'))
    }
} finally { Pop-Location }
