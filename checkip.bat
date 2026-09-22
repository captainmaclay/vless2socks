@echo off
REM Proverka podmeny IP: pryamoy adres vs adres v tunnele
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% main.py --ip -c config.json %*
pause
