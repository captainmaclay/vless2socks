@echo off
REM Proverka tunnelya bez zapuska proxy
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% main.py -c config.json --test %*
pause
