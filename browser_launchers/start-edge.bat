@echo off
REM Jarvis launcher: Microsoft Edge with tab tracking enabled.
REM See README.txt in this folder before first use.

set JARVIS_PORT=9224
set JARVIS_PROFILE=%~dp0jarvis-profiles\edge

if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    set EDGE_EXE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    set EDGE_EXE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe
) else (
    echo Could not find Edge at its usual install location.
    echo Edit this file and set EDGE_EXE to your msedge.exe path.
    pause
    exit /b 1
)

echo Starting Edge with tab tracking on port %JARVIS_PORT% ...
if "%~1"=="" (
    start "" "%EDGE_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%"
) else (
    start "" "%EDGE_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%" "%~1"
)
