@echo off
REM Launches the mod manager window.
REM
REM No elevation is requested here. A GOG Galaxy install grants the logged-in user write
REM access to its Games folder, so most players never need administrator rights -- and the
REM manager reports clearly, in the window, when a folder is not writable and says to
REM restart it elevated. Asking up front for rights that are usually unnecessary is both
REM friction and a bad look for an unsigned script.
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0ModManager.ps1"
