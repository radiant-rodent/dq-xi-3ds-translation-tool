@echo off
rem Dragon Quest XI (3DS) translation studio - opens in your browser
cd /d "%~dp0"
python -m dq11tool %*
if errorlevel 1 pause
