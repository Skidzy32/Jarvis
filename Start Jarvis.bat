@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM Start Jarvis.bat -- one-click boot for the whole system.
REM
REM Replaces running these three things by hand in three separate
REM windows:
REM   1. python3 server.py
REM   2. python3 focus_overlay.py
REM   3. one of browser_launchers\start-*.bat
REM
REM Double-click this file. 2.2.1: the server and the countdown card
REM now run with NO console windows at all -- a hidden supervisor
REM (jarvis_supervisor.py, run by Python's windowless pythonw.exe)
REM starts them, writes their output to the logs folder, and links
REM them so one stopping stops the other. This window stays just long
REM enough to ask which browser you want, then closes itself.
REM
REM To close Jarvis: the small x on the countdown card, or
REM Stop Jarvis.bat. If Jarvis is already running, this just opens it
REM in the browser. If the server fails to start, this window stays
REM open and shows you why (from logs\server.log).
REM ============================================================

cd /d "%~dp0"

echo ================================================
echo   Starting Jarvis
echo ================================================
echo.

REM ---- find a REAL, working Python (1.9.3 fix) -----------------
REM v1 of this script trusted "where py/python/python3" to prove
REM Python was installed. That's not good enough: Windows ships
REM fake stub .exe files under those exact names (the "Microsoft
REM Store" App Execution Aliases) that "where" happily finds, but
REM that do nothing except print an install nag when you actually
REM run them. So now each candidate is actually RUN and its output
REM is checked for a real "Python 3.x.x" version line before it's
REM trusted, and if none of them are real, common install folders
REM are searched directly as a fallback.
set "PYCMD="

REM Search real install folders FIRST, by full path -- this can
REM never hit the fake "python"/"python3" Store-alias stubs, because
REM it never goes through those PATH names at all.
for %%B in ("%LOCALAPPDATA%\Programs\Python" "%ProgramFiles%" "%ProgramFiles(x86)%") do (
    if "!PYCMD!"=="" if exist "%%~B" (
        for /f "delims=" %%D in ('dir /b /ad /o-n "%%~B\Python3*" 2^>nul') do (
            if "!PYCMD!"=="" if exist "%%~B\%%D\python.exe" set "PYCMD=%%~B\%%D\python.exe"
        )
    )
)

REM Only if no real install folder was found, fall back to trying
REM py/python/python3 by name and checking their actual output.
if "!PYCMD!"=="" (
    for %%C in (py python python3) do (
        if "!PYCMD!"=="" (
            "%%C" --version >"%TEMP%\jarvis_pycheck.txt" 2>&1
            findstr /r /c:"^Python [0-9]" "%TEMP%\jarvis_pycheck.txt" >nul 2>&1
            if not errorlevel 1 set "PYCMD=%%C"
        )
    )
    del "%TEMP%\jarvis_pycheck.txt" >nul 2>&1
)

REM 4.0.0: no Python? Offer to install it with Windows' own installer
REM (winget), for this user only, then carry straight on.
if "!PYCMD!"=="" (
    echo Jarvis needs Python 3, which is free, and it is not installed yet.
    echo.
    set "CANWINGET="
    where winget >nul 2>&1 && set "CANWINGET=1"
    if defined CANWINGET (
        set "ANS="
        set /p "ANS=Install Python now? It takes a minute or two. [Y/N]: "
        if /i "!ANS!"=="Y" (
            echo.
            echo Installing Python 3.12 from Microsoft's winget...
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
            if "!PYCMD!"=="" echo Python installed. Close this window and double-click Start Jarvis again.
        )
    )
)

if "!PYCMD!"=="" (
    echo Could not find a working Python install on this machine.
    echo.
    echo If you HAVE installed Python before, the most likely cause is
    echo Windows' own fake "python" shortcut getting in the way instead
    echo of the real one. To fix that permanently:
    echo   Settings ^-^> Apps ^-^> Advanced app settings ^-^>
    echo   App execution aliases ^-^> turn OFF the ones for python.exe
    echo   and python3.exe.
    echo.
    echo If you have NOT installed Python, get it from python.org and
    echo tick "Add python.exe to PATH" during setup.
    echo.
    echo Then run this file again.
    pause
    exit /b 1
)
echo Using Python: !PYCMD!

REM ---- 1. already running? then just open it ---------------------
REM (The checks live in jarvis_supervisor.py as small commands, so this
REM file never has to build fragile quoted one-liners.)
"!PYCMD!" jarvis_supervisor.py --is-running
if not errorlevel 1 (
    echo Jarvis is already running -- opening it in your browser.
    goto :browser
)

REM ---- 2. start everything hidden ----------------------------------
REM pythonw.exe sits next to python.exe in a normal install and runs
REM with no window. If it's somehow missing, fall back to one
REM minimized window rather than failing.
set "PYW="
for %%F in ("!PYCMD!") do set "PYDIR=%%~dpF"
if exist "!PYDIR!pythonw.exe" set "PYW=!PYDIR!pythonw.exe"
echo Starting Jarvis in the background ...
if defined PYW (
    start "" "!PYW!" jarvis_supervisor.py
) else (
    start /min "Jarvis" "!PYCMD!" jarvis_supervisor.py
)

REM ---- 3. make sure it actually came up ----------------------------
REM Hidden processes fail silently, so check: wait up to 20 seconds
REM for the server to answer. If it doesn't, keep this window open
REM and show the end of the server's log.
"!PYCMD!" jarvis_supervisor.py --wait 20
if errorlevel 1 (
    echo.
    echo Jarvis didn't start. The end of logs\server.log says:
    echo ------------------------------------------------
    "!PYCMD!" jarvis_supervisor.py --tail
    echo ------------------------------------------------
    echo.
    echo Close this window when you've read it.
    pause
    exit /b 1
)
echo Jarvis is running.

:browser
REM ---- 4. ask which browser, if any, to open with tab tracking -
echo.
echo Which browser do you want to use today?
echo   1) Opera GX   ^(tab tracking on^)
echo   2) Chrome     ^(tab tracking on^)
echo   3) Edge       ^(tab tracking on^)
echo   4) Brave      ^(tab tracking on^)
echo   5) Just open my default browser ^(no tab tracking today^)
echo.
set /p BROWSER_CHOICE="Type a number and press Enter: "

set JARVIS_URL=http://localhost:4700

if "%BROWSER_CHOICE%"=="1" (
    call "%~dp0browser_launchers\start-opera-gx.bat" "%JARVIS_URL%"
) else if "%BROWSER_CHOICE%"=="2" (
    call "%~dp0browser_launchers\start-chrome.bat" "%JARVIS_URL%"
) else if "%BROWSER_CHOICE%"=="3" (
    call "%~dp0browser_launchers\start-edge.bat" "%JARVIS_URL%"
) else if "%BROWSER_CHOICE%"=="4" (
    call "%~dp0browser_launchers\start-brave.bat" "%JARVIS_URL%"
) else (
    echo Opening your default browser ^(no tab tracking today^) ...
    start "" "%JARVIS_URL%"
)

echo.
echo ================================================
echo   Jarvis is running in the background -- no windows needed.
echo   - The small countdown card sits top-right of your screen. Its
echo     PAUSE / LOCK THIS TAB / ABORT buttons wake up during a session.
echo   - To close Jarvis: the small x on the card, or Stop Jarvis.bat.
echo   - If something seems wrong, the logs folder has what happened.
echo   - This window will close itself in a few seconds.
echo ================================================
echo.
timeout /t 8
