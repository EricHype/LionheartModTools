@echo off
REM Double-click to open the Lionheart map editor. It asks which .zax map to open. You
REM can also drop a .zax file onto this.
REM
REM It shares its setup with Item Editor.bat: a private Python environment in
REM %LOCALAPPDATA%\LionheartModTools\venv with PySide6 in it. If the item editor already
REM opens, this needs no download. Otherwise the first run downloads about 100 MB, once.
REM
REM It needs Python 3.9 or newer. If Python is missing, it says so and opens the download
REM page. Nothing is installed system-wide, and deleting that venv folder undoes the setup.
setlocal
set "HERE=%~dp0"
if defined ITEMEDIT_VENV (set "VENV=%ITEMEDIT_VENV%") else (set "VENV=%LOCALAPPDATA%\LionheartModTools\venv")

if exist "%VENV%\Scripts\pythonw.exe" (
    "%VENV%\Scripts\python.exe" -c "import PySide6" >nul 2>&1 && goto run
)

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo The map editor needs Python 3.9 or newer, and it is not installed.
    echo.
    echo Opening the download page. Install Python, tick "Add python.exe to PATH" on the
    echo first screen of the installer, then double-click Map Editor.bat again.
    echo.
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
)

echo Setting up the map editor. This happens once and downloads about 100 MB...
if not exist "%VENV%\Scripts\python.exe" (
    %PY% -m venv --system-site-packages "%VENV%" || goto fail
)
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r "%HERE%requirements.txt" || goto fail
"%VENV%\Scripts\python.exe" -c "import PySide6" || goto fail

:run
start "" "%VENV%\Scripts\pythonw.exe" "%HERE%mapedit.py" %*
exit /b 0

:fail
echo.
echo Setup did not finish. Check your internet connection and try again.
echo If it keeps failing, delete "%VENV%" and run Map Editor.bat again.
pause
exit /b 1
