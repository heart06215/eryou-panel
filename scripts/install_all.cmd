@echo off
setlocal
net session >nul 2>&1
if errorlevel 1 (
    echo Please run this script as Administrator.
    echo Right-click install_all.cmd - Run as administrator.
    pause
    exit /b 1
)
cd /d "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Project all
if errorlevel 1 pause
