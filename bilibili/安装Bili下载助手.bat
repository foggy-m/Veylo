@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0native\BiliBridge.exe" (
  call "%~dp0native\build.bat"
  if errorlevel 1 exit /b 1
)
python -B install_extension.py %*
echo.
pause
