@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo .NET Framework C# compiler was not found.
  exit /b 1
)
"%CSC%" /nologo /target:exe /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll /out:BiliBridge.exe BiliBridge.cs
if errorlevel 1 exit /b 1
echo Native download bridge compiled.
exit /b 0
