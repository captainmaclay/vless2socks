@echo off
REM Diagnostika: gde imenno rvyotsya cepochka
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% main.py --doctor -c config.json %*
pause
