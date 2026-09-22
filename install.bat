@echo off
REM Pervonachalnaya ustanovka vless2socks: .venv, zavisimosti, xray-core, konfigi.
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

echo.
echo ==================================================
echo    vless2socks - ustanovka
echo ==================================================
echo.

REM ---------- 1. Python 3.10+ ----------
echo [1/5] Ishchu Python 3.10+...
set "PY="
py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :py_ok
)
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :py_ok
)
python3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python3"
    goto :py_ok
)
echo       [!] Python 3.10 ili novee ne najden.
echo           Postavte ego s https://www.python.org/downloads/
echo           i vklyuchite galochku "Add python.exe to PATH".
goto :fail

:py_ok
echo       OK: %PY%

REM ---------- 2. .venv ----------
echo [2/5] Gotovlyu virtualnoe okruzhenie .venv...
if exist ".venv\Scripts\python.exe" (
    echo       Uzhe est - propuskayu
    goto :venv_ok
)
%PY% -m venv ".venv"
if errorlevel 1 (
    echo       [!] Ne udalos sozdat .venv
    goto :fail
)
echo       OK

:venv_ok
set "VPY=.venv\Scripts\python.exe"

REM ---------- 3. Zavisimosti ----------
echo [3/5] Stavlyu zavisimosti iz requirements.txt...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo       [!] pip install ne proshel - proverte internet ili proxy.
    goto :fail
)
"%VPY%" -c "import pystray, PIL, cryptography" >nul 2>&1
if errorlevel 1 (
    echo       [!] Zavisimosti postavilis, no ne importiruyutsya.
    goto :fail
)
echo       OK

REM ---------- 4. xray-core ----------
echo [4/5] Proveryayu xray-core v bin\...
if exist "bin\xray.exe" (
    echo       Uzhe est - propuskayu
    goto :xray_done
)
"%VPY%" -m tools.get_xray
if errorlevel 1 (
    echo       [!] Avtomaticheski skachat ne polychilos.
    echo           Polozhite xray.exe vruchnuyu v papku bin\
    echo           https://github.com/XTLS/Xray-core/releases
    echo           Bez nego rabotaet tolko vstroennyj Python-backend
    echo           (tcp+tls); reality/xtls/ws trebuyut xray.
    goto :xray_done
)
echo       OK

:xray_done

REM ---------- 5. Konfigi ----------
echo [5/5] Sozdayu konfigi iz shablonov (esli ikh net)...
if not exist "config.json"    copy /y "config.example.json"    "config.json"    >nul
if not exist "instances.json" copy /y "instances.example.json" "instances.json" >nul
if not exist "settings.json"  copy /y "settings.example.json"  "settings.json"  >nul
if not exist ".env"           copy /y ".env.example"           ".env"           >nul
echo       OK

echo.
echo ==================================================
echo    Gotovo. Zapusk: start.bat
echo.
echo    Svoyu vless:// ssylku vstavte v GUI
echo    (tab Proxies) libo v instances.json
echo ==================================================
echo.
pause
exit /b 0

:fail
echo.
echo Ustanovka prervana.
echo.
pause
exit /b 1
