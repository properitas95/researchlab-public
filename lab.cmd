@echo off
set PYTHONUTF8=1
where py >nul 2>&1
if %errorlevel%==0 (py -3 "%~dp0lab.py" %*) else (python "%~dp0lab.py" %*)
