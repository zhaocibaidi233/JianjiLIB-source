@echo off
REM ============================================================
REM  Jianji - one-time database initialization
REM
REM  Creates the database if missing, creates tables, runs the
REM  idempotent migration scripts, then loads the CLC class table.
REM
REM  Prerequisite: PostgreSQL installed and running, and the
REM  password reachable via one of (see backend/app/db.py):
REM    1) JIANJI_DATABASE_URL   full connection string
REM    2) JIANJI_DB_PASSWORD    password only
REM    3) libpq default             %APPDATA%\postgresql\pgpass.conf
REM
REM  Safe to re-run. Existing tables/columns are left alone, and
REM  the class table is only loaded when it is still empty.
REM
REM  Options are forwarded, e.g.:
REM    init-database.bat --check        probe only, writes nothing
REM    init-database.bat --force-clc    reload the class table
REM
REM  ASCII-only in this file itself, but the Python script below prints
REM  Chinese; switch the console to UTF-8 so it renders instead of
REM  showing mojibake under the default GBK codepage.
REM ============================================================
setlocal
cd /d "%~dp0"

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

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
echo.

echo [jianji] Portable mode: use JianjiLauncher.exe. This script is for external PostgreSQL or advanced maintenance.

REM --- fail fast with a clear message instead of a stack trace ---
"%PYTHON%" -c "import psycopg2" 2>nul
if errorlevel 1 (
    echo [jianji] ERROR: Python dependency psycopg2 is missing.
    echo [jianji] Install backend deps first:
    echo [jianji]   "%PYTHON%" -m pip install -r requirements.lock.txt
    echo.
    pause
    exit /b 1
)

"%PYTHON%" scripts\init_db.py %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [jianji] OK - database is ready.
) else (
    echo [jianji] FAILED - read the messages above.
)
echo.
pause
exit /b %RC%
