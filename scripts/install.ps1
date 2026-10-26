param(
    [ValidateSet("all", "ok-nte", "ok-ww-enhanced", "maa", "MAA")]
    [string]$Project = "all",
    [string]$Root = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "请以管理员身份运行 PowerShell，再执行本脚本（游戏自动化需要管理员权限）。" -ForegroundColor Yellow
    exit 1
}

$panelRoot = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $panelRoot "EryouPanel.exe"
if (-not (Test-Path $exe)) {
    $exe = Join-Path $panelRoot "dist\EryouPanel.exe"
}

if (-not $Root) { $Root = $panelRoot }

if (Test-Path $exe) {
    $exeArgs = @("--install", $Project, "--root", $Root)
    if ($Force) { $exeArgs += "--force" }
    & $exe @exeArgs
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "EryouPanel.exe not found and Python is not available. Build the EXE first: .\build.ps1"
}
$sourceArgs = @("app.py", "--install", $Project, "--root", $Root)
if ($Force) { $sourceArgs += "--force" }
& $python.Source @sourceArgs
exit $LASTEXITCODE
