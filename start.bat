@echo off
REM Zapusk GUI vless2socks. Esli ustanovki eshche ne bylo - vyzyvaet install.bat.
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Okruzhenie .venv ne najdeno - zapuskayu ustanovku.
    echo.
    call "install.bat"
    if errorlevel 1 exit /b 1
)

set "VPY=.venv\Scripts\python.exe"
set "VPYW=.venv\Scripts\pythonw.exe"

REM Zavisimosti mogli ne doustanovitsya ranshe
"%VPY%" -c "import pystray, PIL, cryptography" >nul 2>&1
if errorlevel 1 (
    echo Doustanavlivayu zavisimosti...
    "%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo [!] Ne udalos postavit zavisimosti - zapustite install.bat
        pause
        exit /b 1
    )
)

if not exist "bin\xray.exe" (
    echo [i] bin\xray.exe ne najden: profili reality / xtls / ws rabotat ne budut.
    echo     Skachat: install.bat
)

start "" "%VPYW%" gui.py
exit /b 0
