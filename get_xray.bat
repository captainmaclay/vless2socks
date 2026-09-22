@echo off
REM Skachat xray-core v papku bin/ s proverkoy SHA-256
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% tools\get_xray.py %*
pause
