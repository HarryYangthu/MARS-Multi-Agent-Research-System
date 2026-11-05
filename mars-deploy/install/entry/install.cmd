@echo off
rem MARS offline install (Windows 10/11 x64). No network access, no administrator rights needed.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%runtime\python\python.exe"
if not exist "%PY%" (
  if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
  tar -xzf "%ROOT%bundle\python.tar.gz" -C "%ROOT%runtime" || (echo Failed to unpack the bundled Python & exit /b 1)
)
"%PY%" -I "%ROOT%code\mars-deploy\install\install.py" %*
set "CODE=%errorlevel%"
pause
exit /b %CODE%
