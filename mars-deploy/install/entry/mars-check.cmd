@echo off
rem MARS: run.py check (see code\mars-deploy\install\run.py)
"%~dp0runtime\python\python.exe" -I "%~dp0code\mars-deploy\install\run.py" check %*
