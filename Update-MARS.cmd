@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0deploy\windows-native\Update.ps1"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0deploy\windows-native\Update.ps1" -Source "%~1"
)
set "mars_exit=%ERRORLEVEL%"
if not "%mars_exit%"=="0" echo Update failed. Your dependency folder, settings and research data were not erased.
pause
exit /b %mars_exit%
