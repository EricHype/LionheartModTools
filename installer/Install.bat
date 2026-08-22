@echo off
REM Double-clickable installer. Self-elevates: a GOG or Steam install normally lives
REM under Program Files, and writing there needs administrator rights. Without this the
REM copy fails partway through with an access-denied error that looks like a broken
REM download rather than a permissions problem.
setlocal

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mod-installer.ps1" -Action install -ModDir "%~dp0."

echo.
pause
