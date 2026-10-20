@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0native\ShiguangBridge.exe" (
  call "%~dp0native\build.bat"
  if errorlevel 1 goto fail
)
python -B "%~dp0install_extension.py"
if errorlevel 1 goto fail
echo.
echo 浏览器中加载扩展目录：%~dp0extension
pause
exit /b 0
:fail
echo 安装失败，请查看上面的错误。
pause
exit /b 1
