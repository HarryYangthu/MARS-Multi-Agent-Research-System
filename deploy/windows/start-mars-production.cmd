@echo off
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0Start-Mars.ps1" -Production %*
set "MARS_EXIT_CODE=%ERRORLEVEL%"
if not "%MARS_EXIT_CODE%"=="0" pause
exit /b %MARS_EXIT_CODE%
