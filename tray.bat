@echo off
REM Zapusk tray-vidzheta vless2socks
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM Proverka zavisimostej
%PY% -c "import pystray, PIL" 2>nul
if errorlevel 1 (
    echo Ustanavlivayu zavisimosti dlya tray-vidzheta...
    %PY% -m pip install pystray Pillow --quiet
)

start "" /B %PY% tray_widget.py
