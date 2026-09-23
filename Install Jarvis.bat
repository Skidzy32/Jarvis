@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM Install Jarvis.bat -- run this ONCE after unzipping.
REM
REM It finds Python (and offers to install it if it's missing), then
REM installs Jarvis for you: a Jarvis icon on your Desktop and in the
REM Start menu, and an entry in Settings > Apps with Uninstall. After
REM that you never need this folder again -- open Jarvis from its icon.
REM Running it again from a newer download updates Jarvis; your notes
REM and settings are kept.
REM
REM   Install Jarvis.bat /inplace   (used by Setup.exe: no questions)
REM ============================================================
cd /d "%~dp0"
set "UNATTENDED="
set "MODE="
if /i "%~1"=="/inplace" (
    set "UNATTENDED=1"
    set "MODE=--in-place"
)

echo ================================================
echo   Installing Jarvis
echo ================================================
echo.

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
        if defined UNATTENDED set "ANS=Y"
        if not defined UNATTENDED set /p "ANS=Install Python now? It takes a minute or two. [Y/N]: "
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
    if not defined UNATTENDED pause
    exit /b 1
)
echo Using Python: !PYCMD!
echo.

if defined MODE (
    "!PYCMD!" installer.py --in-place
) else (
    "!PYCMD!" installer.py
)
if errorlevel 1 (
    echo.
    echo Something went wrong -- the message above says what.
    if not defined UNATTENDED pause
    exit /b 1
)
echo.
echo Done. Jarvis is starting, and from now on you can open it from the
echo Jarvis icon on your Desktop or in the Start menu.
if not defined UNATTENDED timeout /t 8
exit /b 0
