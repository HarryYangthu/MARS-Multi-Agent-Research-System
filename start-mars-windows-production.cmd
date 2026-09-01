@echo off
echo This native Windows branch does not expose production mode because Windows
echo cannot enforce the Linux read-only mount boundary required by MARS production.
echo Use the CPU development/staging launcher: start-mars-windows.cmd
pause
exit /b 2
