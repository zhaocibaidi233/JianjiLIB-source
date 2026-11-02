@echo off
setlocal
cd /d "%~dp0"

REM NOTE: keep this file ASCII-only. cmd reads .bat in the OEM codepage (GBK on
REM zh-CN Windows); UTF-8 Chinese comments get mangled and their fragments are
REM executed as commands ("'ut' is not recognized..."). Chinese notes live in
REM app.manifest and CHANGELOG.md instead.

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo [launcher] csc.exe not found. Install .NET Framework 4.8.
    exit /b 1
)

if not exist "jianji.ico" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-icon.ps1"
    if errorlevel 1 echo [launcher] icon generation failed; building without icon.
)

REM Generate Version.cs from ..\VERSION first: a build step cannot forget to bump it.
REM (The attributes used to be hand-edited and drifted twice in one day.)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0version-gen.ps1"
if errorlevel 1 (
    echo [launcher] version generation failed.
    exit /b 1
)

set "OUT=%~dp0..\JianjiLauncher.exe"
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

REM Optional args (icon / manifest) are NOT collected into a variable: a variable
REM holding quotes gets re-parsed on expansion and an empty one becomes a bare
REM "" which csc treats as the program name.
echo [launcher] building...
if exist "%HERE%\app.manifest" goto :with_manifest
goto :without_manifest

:with_manifest
if exist "%HERE%\jianji.ico" (
    "%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 ^
      /win32icon:"%HERE%\jianji.ico" /win32manifest:"%HERE%\app.manifest" ^
      /out:"%OUT%" ^
      /r:System.dll /r:System.Core.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll /r:System.Web.Extensions.dll ^
      "%HERE%\Launcher.cs" "%HERE%\LauncherConfig.cs" "%HERE%\Ui.cs" "%HERE%\Version.cs"
) else (
    "%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 ^
      /win32manifest:"%HERE%\app.manifest" ^
      /out:"%OUT%" ^
      /r:System.dll /r:System.Core.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll /r:System.Web.Extensions.dll ^
      "%HERE%\Launcher.cs" "%HERE%\LauncherConfig.cs" "%HERE%\Ui.cs" "%HERE%\Version.cs"
)
goto :done

:without_manifest
if exist "%HERE%\jianji.ico" (
    "%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 ^
      /win32icon:"%HERE%\jianji.ico" ^
      /out:"%OUT%" ^
      /r:System.dll /r:System.Core.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll /r:System.Web.Extensions.dll ^
      "%HERE%\Launcher.cs" "%HERE%\LauncherConfig.cs" "%HERE%\Ui.cs" "%HERE%\Version.cs"
) else (
    "%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 ^
      /out:"%OUT%" ^
      /r:System.dll /r:System.Core.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll /r:System.Web.Extensions.dll ^
      "%HERE%\Launcher.cs" "%HERE%\LauncherConfig.cs" "%HERE%\Ui.cs" "%HERE%\Version.cs"
)

:done
if errorlevel 1 (
    echo [launcher] build failed.
    exit /b 1
)

echo [launcher] OK: %OUT%
