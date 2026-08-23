@echo off
REM Double-clickable installer.
REM
REM Elevation is requested only if it turns out to be needed. A GOG Galaxy install grants
REM the logged-in user write access to its Games folder, so most players never need
REM administrator rights at all -- and demanding them up front is both friction and a bad
REM look for an unsigned script. The installer exits with code 2 if it cannot write to the
REM game folder; only then does this re-launch itself elevated.
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
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mod-installer.ps1" -Action install -ModDir "%~dp0."
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
