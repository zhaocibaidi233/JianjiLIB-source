@echo off
REM ============================================================
REM  Jianji - start the MCP server (stdio transport)
REM
REM  The MCP server is NOT a window you keep open: an MCP client
REM  (Claude Desktop / Cursor / Cherry Studio ...) launches it and
REM  talks JSON-RPC over stdin/stdout. So this script is mostly a
REM  launcher + a config helper:
REM
REM    start-mcp.bat            show the client config + self-check
REM    start-mcp.bat run        run the server in THIS window (debug)
REM    start-mcp.bat check      self-check only (tools + backend ping)
REM
REM  Notes:
REM  - Output is ASCII-only on purpose: the console codepage would
REM    otherwise garble non-English text.
REM  - Delays use `ping` rather than `timeout.exe`, for two reasons:
REM      1. Git Bash / MSYS on PATH ships a GNU `timeout` that shadows
REM         Windows' one and rejects the /t switch.
REM      2. Windows' timeout.exe aborts with "input redirection is not
REM         supported" whenever stdin is redirected (pipes, CI, <nul).
REM  - curl is called by full path for the same shadowing reason.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "API_URL=http://127.0.0.1:8000"
if defined JIANJI_API_URL set "API_URL=%JIANJI_API_URL%"
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

REM UTF-8 both ways: the tool payloads carry Chinese book data.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist "%~dp0..\runtime\python\Lib\site-packages\mcp" (
    echo [jianji] NOTE: portable runtime does not include MCP deps.
    echo [jianji] Use an external Python with requirements-mcp.lock.txt for MCP.
)

set "MODE=%~1"
if /I "%MODE%"=="run" goto run
if /I "%MODE%"=="check" goto check
goto help

REM ============================================================
REM  Default: print the config to paste into the MCP client, then
REM  run a self-check so the user knows whether it will work.
REM ============================================================
:help
echo [jianji] python : %PYTHON%
echo [jianji] folder : %CD%
echo [jianji] api url: %API_URL%
if defined JIANJI_API_TOKEN (
    echo [jianji] token  : set
) else (
    echo [jianji] token  : NOT set  ^(no auth headers will be sent^)
)
echo.
echo ------------------------------------------------------------
echo  Paste this into your MCP client config file:
echo ------------------------------------------------------------
echo.
echo   "mcpServers": {
echo     "jianji": {
echo       "command": "%PYTHON:\=\\%",
echo       "args": ["-m", "app.mcp_server"],
echo       "cwd": "%CD:\=\\%",
echo       "env": {
echo         "PYTHONIOENCODING": "utf-8",
echo         "JIANJI_API_URL": "%API_URL%"
echo       }
echo     }
echo   }
echo.
echo  Add these lines inside "env" if your backend needs credentials:
echo     "JIANJI_API_TOKEN": "your-access-token-or-login-token"
echo.
echo  Claude Desktop : %%APPDATA%%\Claude\claude_desktop_config.json
echo  Cursor         : .cursor\mcp.json  (per project^)
echo  Cherry Studio  : Settings - MCP Servers - Add (stdio^)
echo.
echo  Manual test    : start-mcp.bat run
echo                   start-mcp.bat check
echo.
goto check

REM ============================================================
REM  check: self-check without entering the stdio loop.
REM ============================================================
:check
if not exist "%CURL%" set "CURL=curl"
echo [jianji] checking backend %API_URL%/api/health ...
"%CURL%" --noproxy "*" -f -s -o nul -m 3 "%API_URL%/api/health"
if errorlevel 1 (
    echo [jianji] WARNING: backend did not answer at %API_URL%
    echo [jianji] The MCP server will still start, but every tool call
    echo [jianji] will fail until the backend is up. Run restart-backend.bat,
    echo [jianji] or fix JIANJI_API_URL if the backend lives elsewhere.
    echo.
)
echo.
"%PYTHON%" -m app.mcp_server --check
if errorlevel 1 (
    echo.
    echo [jianji] Self-check failed. Most likely the optional MCP package
    echo [jianji] is not installed yet. Install it with:
    echo [jianji]     "%PYTHON%" -m pip install -r requirements-mcp.lock.txt
)
echo.
if /I "%MODE%"=="check" goto done
goto done

REM ============================================================
REM  run: run the server in this window. It will look like it is
REM  frozen - that is correct: it is waiting for JSON-RPC on stdin.
REM  Ctrl+C to stop.
REM ============================================================
:run
echo [jianji] starting MCP server on stdio (Ctrl+C to stop)
echo [jianji] this window will look idle: it waits for the MCP client
echo.
"%PYTHON%" -m app.mcp_server
echo.
echo [jianji] MCP server exited (code %ERRORLEVEL%)

:done
echo.
pause
