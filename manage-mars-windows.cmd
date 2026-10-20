@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0deploy\windows-native\Mars.ps1" -Action Menu -OfflineRequired %*
set "mars_exit=%ERRORLEVEL%"
pause
exit /b %mars_exit%
