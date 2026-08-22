@echo off
REM Restores every file the installer replaced and removes every file it added. See the
REM note in Install.bat for why this needs administrator rights.
setlocal

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mod-installer.ps1" -Action uninstall -ModDir "%~dp0."

echo.
pause
