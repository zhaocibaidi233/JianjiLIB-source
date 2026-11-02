@echo off
REM ============================================================
REM  Jianji - restart backend (stop old + start new)
REM
REM  Double-click to run, or call it from a terminal.
REM  Also works as a plain launcher when nothing is running yet.
REM
REM  Notes:
REM  - Output is ASCII-only on purpose: the console codepage would
REM    otherwise garble non-English text.
REM  - Delays use `ping` rather than `timeout.exe`, for two reasons:
REM      1. Git Bash / MSYS on PATH ships a GNU `timeout` that shadows
REM         Windows' one and rejects the /t switch.
REM      2. Windows' timeout.exe aborts with "input redirection is not
REM         supported" whenever stdin is redirected (pipes, CI, <nul).
REM    `ping -n N 127.0.0.1` waits about N-1 seconds and is unaffected.
REM  - curl is called by full path for the same shadowing reason.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PORT=8000"
set "CURL=%SystemRoot%\System32\curl.exe"

REM --- locate python: project venv first, then common per-user installs, then PATH ---
set "PYTHON="
if exist "%~dp0..\runtime\python\python.exe" set "PYTHON=%~dp0..\runtime\python\python.exe"
if not defined PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON if exist "%~dp0venv\Scripts\python.exe" set "PYTHON=%~dp0venv\Scripts\python.exe"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYTHON set "PYTHON=python"
set "PYTHONPATH=%~dp0"
set "PATH=%~dp0..\runtime\pgsql\bin;%PATH%"

echo [jianji] python : %PYTHON%
echo [jianji] folder : %CD%
echo [jianji] Portable mode: use JianjiLauncher.exe to start/stop the bundled database.
echo.

REM ---------- 1) stop whatever is listening on the port ----------
set "KILLED="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        echo [jianji] stopping old process pid=%%P
        taskkill /PID %%P /F >nul 2>&1
        set "KILLED=1"
    )
)
if not defined KILLED echo [jianji] port %PORT% was free

REM give the OS a moment to release the socket (~2s)
ping -n 3 127.0.0.1 >nul

REM ---------- 2) start a fresh backend ----------
echo [jianji] starting: uvicorn app.main:app --port %PORT%
start "jianji-backend" "%PYTHON%" -m uvicorn app.main:app --port %PORT%

REM ---------- 3) wait until it answers /api/health ----------
if not exist "%CURL%" set "CURL=curl"
echo [jianji] waiting for http://127.0.0.1:%PORT%/api/health ...
set /a TRIES=0
:wait
set /a TRIES+=1
ping -n 2 127.0.0.1 >nul
"%CURL%" --noproxy "*" -f -s -o nul -m 3 "http://127.0.0.1:%PORT%/api/health"
if not errorlevel 1 goto up
if %TRIES% GEQ 25 goto timeout
goto wait

:up
echo [jianji] OK - backend is up (took %TRIES%s)
echo [jianji] API   : http://127.0.0.1:%PORT%/docs
echo [jianji] UI    : http://127.0.0.1:%PORT%
echo [jianji] stop  : close the "jianji-backend" window, or Ctrl+C in it
goto done

:timeout
echo.
echo [jianji] WARNING: no response after %TRIES%s.
echo [jianji] Check the "jianji-backend" window for the real error
echo [jianji] (common causes: port still held, missing deps, wrong python).

:done
echo.
pause
