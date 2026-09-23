@echo off
REM Jarvis launcher: Opera GX with tab tracking enabled.
REM See README.txt in this folder before first use.

set JARVIS_PORT=9222
set JARVIS_PROFILE=%~dp0jarvis-profiles\opera-gx

if exist "%LOCALAPPDATA%\Programs\Opera GX\opera.exe" (
    set OPERA_GX_EXE=%LOCALAPPDATA%\Programs\Opera GX\opera.exe
) else if exist "%LOCALAPPDATA%\Programs\Opera GX\launcher.exe" (
    set OPERA_GX_EXE=%LOCALAPPDATA%\Programs\Opera GX\launcher.exe
) else (
    echo Could not find Opera GX at its usual install location.
    echo Edit this file and set OPERA_GX_EXE to your opera.exe path.
    pause
    exit /b 1
)

echo Starting Opera GX with tab tracking on port %JARVIS_PORT% ...
if "%~1"=="" (
    start "" "%OPERA_GX_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%"
) else (
    start "" "%OPERA_GX_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%" "%~1"
)
