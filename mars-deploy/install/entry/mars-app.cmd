@echo off
rem MARS: run.py app (see code\mars-deploy\install\run.py)
"%~dp0runtime\python\python.exe" -I "%~dp0code\mars-deploy\install\run.py" app %*
