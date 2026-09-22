@echo off
REM Zapusk multi-tab GUI vless2socks
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    set "PY=.venv\Scripts\pythonw.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=pythonw"
)

REM Proverka zavisimostej (pystray + Pillow)
".venv\Scripts\python.exe" -c "import pystray, PIL" 2>nul
if errorlevel 1 (
    echo Ustanavlivayu pystray i Pillow...
    ".venv\Scripts\python.exe" -m pip install pystray Pillow --quiet
)

start "" %PY% gui.py
