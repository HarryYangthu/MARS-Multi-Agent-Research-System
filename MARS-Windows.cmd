@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0deploy\windows-native\Mars.ps1" -Action Start %*
set "mars_exit=%ERRORLEVEL%"
if not "%mars_exit%"=="0" echo MARS failed. Read the error above and deploy\windows-native\README.md.
pause
exit /b %mars_exit%
