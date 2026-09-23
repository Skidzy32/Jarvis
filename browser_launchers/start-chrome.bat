@echo off
REM Jarvis launcher: Google Chrome with tab tracking enabled.
REM See README.txt in this folder before first use.

set JARVIS_PORT=9223
set JARVIS_PROFILE=%~dp0jarvis-profiles\chrome

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
) else (
    echo Could not find Chrome at its usual install location.
    echo Edit this file and set CHROME_EXE to your chrome.exe path.
    pause
    exit /b 1
)

echo Starting Chrome with tab tracking on port %JARVIS_PORT% ...
if "%~1"=="" (
    start "" "%CHROME_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%"
) else (
    start "" "%CHROME_EXE%" --remote-debugging-port=%JARVIS_PORT% --user-data-dir="%JARVIS_PROFILE%" "%~1"
)
