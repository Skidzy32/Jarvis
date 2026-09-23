@echo off
setlocal
REM ============================================================
REM Stop Jarvis.bat -- the backup way to close Jarvis (2.2.1).
REM
REM Since 2.2.1 Jarvis runs with no console windows, so there's no
REM window to close. The usual way is the small x on the countdown
REM card. This file does the same from outside:
REM   1. leaves a logs\stop.request file; the hidden supervisor sees
REM      it within a second and shuts down the normal way (a running
REM      focus session is ended and saved first);
REM   2. only if that hasn't worked after ~10 seconds, force-stops it --
REM      and only after checking the saved process id really belongs
REM      to Python, so a stale id can never stop an unrelated program.
REM ============================================================

cd /d "%~dp0"
set "PIDFILE=logs\jarvis.pid"

if not exist "%PIDFILE%" (
    echo Jarvis doesn't seem to be running.
    goto :done
)

set /p JPID=<"%PIDFILE%"
tasklist /FI "PID eq %JPID%" /NH | findstr /I "python" >nul
if errorlevel 1 (
    echo Jarvis isn't running any more.
    del "%PIDFILE%" >nul 2>&1
    goto :done
)

echo Stopping Jarvis ...
echo stop> "logs\stop.request"

REM Wait up to ~10 seconds for it to finish on its own.
for /L %%i in (1,1,10) do (
    if not exist "%PIDFILE%" goto :stopped
    timeout /t 1 /nobreak >nul
)

echo It didn't stop on its own -- stopping it directly.
tasklist /FI "PID eq %JPID%" /NH | findstr /I "python" >nul
if not errorlevel 1 taskkill /PID %JPID% /T /F >nul 2>&1
del "%PIDFILE%" >nul 2>&1
del "logs\stop.request" >nul 2>&1

:stopped
echo Jarvis stopped.

:done
echo.
timeout /t 4
