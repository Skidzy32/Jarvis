@echo off
REM Jarvis launcher: Brave with tab tracking enabled.
REM See README.txt in this folder before first use.

set JARVIS_PORT=9225
set JARVIS_PROFILE=%~dp0jarvis-profiles\brave

if exist "%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe" (
    set BRAVE_EXE=%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe
) else if exist "%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe" (
    set BRAVE_EXE=%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe
) else (
    echo Could not find Brave at its usual install location.
    echo Edit this file and set BRAVE_EXE to your brave.exe path.
    pause
    exit /b 1
)

echo Starting Brave with tab tracking on port %JARVIS_PORT% ...
if "%~1"=="" (
    start "" "%BRAVE_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%"
) else (
    start "" "%BRAVE_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%" "%~1"
)
