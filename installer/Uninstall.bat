@echo off
REM Double-clickable uninstaller.
REM
REM Restores every file the installer replaced and removes every file it added.
REM Elevation is requested only if it turns out to be needed -- see Install.bat.
setlocal

if "%~1"=="elevated" goto :run

call :run
if errorlevel 2 (
    echo.
    echo This game folder needs administrator rights.
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -WorkingDirectory '%~dp0' -Verb RunAs"
)
goto :eof

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mod-installer.ps1" -Action uninstall -ModDir "%~dp0."
if "%~1"=="elevated" (
    echo.
    pause
) else (
    if not errorlevel 2 (
        echo.
        pause
    )
)
goto :eof
