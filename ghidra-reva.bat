@echo off
setlocal EnableDelayedExpansion
rem ===========================================================================
rem  ghidra-reva.bat -- start Ghidra with ReVa's MCP server actually bound, then
rem  optionally start Claude Code, in that order.
rem
rem  WHY THIS EXISTS
rem
rem  ReVa has two plugins and only one of them serves MCP:
rem
rem    RevaApplicationPlugin  front end. Calls startServer / frontEndService /
rem                           getActiveProject. THIS is what binds 127.0.0.1:8080.
rem                           Must be enabled in the Ghidra PROJECT window, with a
rem                           project open.
rem    RevaPlugin             Code Browser. Only consumes the service, and logs
rem                           "RevaMcpService not available" when it is missing.
rem
rem  Enabling the Code Browser one in both dialogs looks right and does nothing.
rem
rem  And Ghidra writes tool configuration only on a CLEAN EXIT (File > Exit). Kill
rem  it, or let it crash, and the enablement is lost silently -- which is exactly
rem  how a working setup turned back into ECONNREFUSED between 1 and 6 October.
rem
rem  Finally: Claude Code connects to MCP servers only at launch. Starting Claude
rem  before the port is bound gives a session with no ReVa tools, and no restart
rem  of Ghidra will fix that session.
rem
rem  USAGE
rem    ghidra-reva.bat save     snapshot the CURRENT FrontEndTool.xml as known-good.
rem                             Run this once, AFTER a clean File > Exit with ReVa
rem                             working, so there is something to restore from.
rem    ghidra-reva.bat          restore the snapshot if the live config has lost
rem                             ReVa, start Ghidra, wait for the port, report.
rem    ghidra-reva.bat claude   the same, then start Claude Code once the port is up.
rem    ghidra-reva.bat check    just report whether the port is bound.
rem ===========================================================================

set "GHIDRA=C:\Users\vkays\Downloads\ghidra_12.0.4_PUBLIC_20260303\ghidra_12.0.4_PUBLIC"
set "SETTINGS=%APPDATA%\ghidra\ghidra_12.0.4_PUBLIC"
set "LIVE=%SETTINGS%\FrontEndTool.xml"
set "GOOD=%~dp0ghidra-reva-FrontEndTool.known-good.xml"
set "LOG=%SETTINGS%\application.log"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=run"

rem --------------------------------------------------------------- check / save
if /i "%MODE%"=="check" goto :check

rem label-based rather than a parenthesised block: `exit /b N` inside `if ( ... )`
rem does not reliably set the exit code, which made `save` report success on refusal
if /i "%MODE%"=="save" goto :save
goto :aftersave

:save
if not exist "%LIVE%" (
  echo [x] no FrontEndTool.xml at "%LIVE%"
)
if not exist "%LIVE%" exit /b 1
findstr /i /c:"RevaApplicationPlugin" "%LIVE%" >nul 2>&1
if not errorlevel 1 goto :savedo
echo [x] the live config does NOT contain RevaApplicationPlugin, so there is
echo     nothing worth saving. Do this first:
echo.
echo       1. open Ghidra and open your project
echo       2. in the PROJECT window: File ^> Configure ^> the plug icon
echo          ^(Configure All Plugins^), search RevaApplicationPlugin, tick it
echo       3. confirm with:  ghidra-reva.bat check
echo       4. File ^> Exit  -- cleanly, so the config is written
echo       5. run:  ghidra-reva.bat save
exit /b 1

:savedo
copy /y "%LIVE%" "%GOOD%" >nul
echo [ok] snapshotted a known-good front-end config containing RevaApplicationPlugin
echo      -^> "%GOOD%"
exit /b 0

:aftersave

rem --------------------------------------------------------------- restore
if exist "%LIVE%" (
  findstr /i /c:"RevaApplicationPlugin" "%LIVE%" >nul 2>&1
  if errorlevel 1 (
    if exist "%GOOD%" (
      echo [*] live front-end config has lost RevaApplicationPlugin -- restoring the snapshot
      copy /y "%LIVE%" "%LIVE%.bak-before-reva-restore" >nul
      copy /y "%GOOD%" "%LIVE%" >nul
      echo     restored. Previous file kept as FrontEndTool.xml.bak-before-reva-restore
    ) else (
      echo [*] live front-end config has no RevaApplicationPlugin and there is no
      echo     snapshot to restore. Enable it by hand this once, then run:
      echo         ghidra-reva.bat save
    )
  ) else (
    echo [ok] live front-end config already contains RevaApplicationPlugin
  )
)

rem --------------------------------------------------------------- launch
tasklist /fi "imagename eq javaw.exe" 2>nul | find /i "javaw.exe" >nul
if not errorlevel 1 (
  echo [i] a javaw process is already running -- not starting a second Ghidra
) else (
  echo [i] starting Ghidra...
  start "" "%GHIDRA%\ghidraRun.bat"
)

rem --------------------------------------------------------------- wait for :8080
echo [i] waiting for ReVa to bind 127.0.0.1:8080 ^(up to 180s^)...
set /a TRIES=0
:wait
set /a TRIES+=1
netstat -ano | findstr /c:"127.0.0.1:8080" | findstr /i "LISTENING" >nul 2>&1
if not errorlevel 1 goto :bound
if %TRIES% GEQ 90 goto :timeout
rem 2s per try, without needing PowerShell
ping -n 3 127.0.0.1 >nul
goto :wait

:timeout
echo.
echo [x] nothing bound 127.0.0.1:8080 after 180s. ReVa's server did not start.
echo     Check the tail of the log for the giveaway line:
echo         findstr /i reva "%LOG%"
echo     "RevaMcpService not available" means RevaApplicationPlugin is still not
echo     loaded in the PROJECT window. Open a project there, then:
echo         File ^> Configure ^> plug icon ^> search RevaApplicationPlugin ^> tick
echo     Then File ^> Exit cleanly and run:  ghidra-reva.bat save
exit /b 1

:bound
echo [ok] ReVa is listening on 127.0.0.1:8080
findstr /i /c:"MCP server started successfully" "%LOG%" >nul 2>&1
if not errorlevel 1 echo [ok] log confirms: MCP server started successfully
if /i "%MODE%"=="claude" (
  echo [i] starting Claude Code -- it binds MCP servers at launch, which is why this waited
  call claude
)
echo.
echo [i] REMEMBER: exit Ghidra with File ^> Exit. Killing it discards the plugin
echo     configuration and ReVa will be disabled again next time.
exit /b 0

:check
netstat -ano | findstr /c:"127.0.0.1:8080" | findstr /i "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [x] nothing is listening on 127.0.0.1:8080 -- ReVa is NOT serving
  if exist "%LOG%" (
    echo     last ReVa lines from the log:
    for /f "tokens=*" %%L in ('findstr /i reva "%LOG%"') do set "LAST=%%L"
    echo       !LAST!
  )
  exit /b 1
)
echo [ok] ReVa is listening on 127.0.0.1:8080
exit /b 0
