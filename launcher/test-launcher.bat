@echo off
setlocal
cd /d "%~dp0"

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo [launcher-tests] csc.exe not found. Install .NET Framework 4.8.
    exit /b 1
)

set "OUT=%TEMP%\jianji-launcher-config-tests.exe"
"%CSC%" /nologo /target:exe /codepage:65001 ^
  /out:"%OUT%" ^
  /r:System.dll /r:System.Core.dll /r:System.Web.Extensions.dll ^
  "%~dp0LauncherConfig.cs" "%~dp0tests\ConfigTests.cs"
if errorlevel 1 (
    echo [launcher-tests] build failed.
    exit /b 1
)

"%OUT%"
if errorlevel 1 (
    echo [launcher-tests] failed.
    exit /b 1
)

echo [launcher-tests] OK
