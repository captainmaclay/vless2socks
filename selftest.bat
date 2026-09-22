@echo off
REM Polnyy progon: ustanovka xray, diagnostika, proverka IP
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% tools\selftest_all.py %*
pause
