@echo off
REM Zapusk vless2socks s konfiguraciej config.json
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
if not exist "config.json" (
    echo config.json not found, creating template...
    %PY% main.py --init-config config.json
    echo.
    echo Put your vless:// link into config.json and run run.bat again.
    pause
    exit /b 1
)
%PY% main.py -c config.json %*
pause
