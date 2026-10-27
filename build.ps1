param(
    [string]$Python = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Resolve-Python {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path $Explicit)) { return $Explicit }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "D:\python\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($item in $candidates) {
        if ($item -and (Test-Path $item)) { return $item }
    }
    throw "未找到 Python。请安装 Python 3.12+，或使用 -Python <路径> 指定解释器。"
}

$py = Resolve-Python -Explicit $Python
Write-Host "Python: $py" -ForegroundColor Cyan

& $py -c "import sys; assert sys.version_info >= (3, 10), sys.version"
if ($LASTEXITCODE -ne 0) { throw "需要 Python 3.10+" }

if (-not $SkipTests) {
    Write-Host "运行测试…" -ForegroundColor Cyan
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "测试失败" }
}

Write-Host "检查 PyInstaller…" -ForegroundColor Cyan
& $py -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    & $py -m pip install pyinstaller --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
}

Write-Host "检查 PySide6 / qfluentwidgets…" -ForegroundColor Cyan
& $py -c "import PySide6, qfluentwidgets, qframelesswindow" *> $null
if ($LASTEXITCODE -ne 0) {
    & $py -m pip install PySide6 PySide6-Fluent-Widgets --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "GUI 依赖安装失败" }
}

if (Test-Path "$root\EryouPanel.exe") { Remove-Item "$root\EryouPanel.exe" -Force }
if (Test-Path "$root\build\EryouPanel") { Remove-Item "$root\build\EryouPanel" -Recurse -Force }

Write-Host "打包 EryouPanel.exe…" -ForegroundColor Cyan
New-Item -ItemType Directory -Force "$root\build" | Out-Null
& $py -m PyInstaller --noconfirm --clean --onefile --console --uac-admin --name EryouPanel `
    --icon "$root\assets\panel.ico" `
    --collect-all qfluentwidgets --collect-all qframelesswindow `
    --collect-all pywinauto --collect-all comtypes `
    --collect-all py7zr `
    --add-data "$root\assets;assets" `
    --exclude-module PIL --exclude-module numpy --exclude-module pygments.formatters.img `
    --exclude-module textual `
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtWebChannel --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtQml --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization `
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf `
    --exclude-module PySide6.QtSql --exclude-module PySide6.QtTest `
    --distpath "$root" --workpath build --specpath build app.py *> "$root\build\pyinstaller.log"
$code = $LASTEXITCODE
Get-Content "$root\build\pyinstaller.log" -Tail 8 | Out-Host
if ($code -ne 0 -or -not (Test-Path "$root\EryouPanel.exe")) { throw "打包失败，详见 build\pyinstaller.log" }

Write-Host "运行打包自检…" -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    & "$root\EryouPanel.exe" --selftest
    if ($LASTEXITCODE -ne 0) { throw "自检失败" }
} else {
    Write-Host "EXE 已嵌入 requireAdministrator；当前会话非管理员，跳过启动自检（避免 UAC 弹窗）" -ForegroundColor Yellow
    Write-Host "源码测试与 PyInstaller 构建均已通过。" -ForegroundColor Yellow
}

$size = [math]::Round((Get-Item "$root\EryouPanel.exe").Length / 1MB, 1)
Write-Host "完成：EryouPanel.exe（$size MB）" -ForegroundColor Green
