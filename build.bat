@echo off
REM Sborka reliza: vless2socks.exe (GUI) + vless2socks-cli.exe (rabochij process).
REM Sam dostavlyaet .venv, zavisimosti i PyInstaller, esli ikh net.
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

echo.
echo ==================================================
echo    vless2socks - sborka reliza
echo ==================================================
echo.

REM ---------- 1. Okruzhenie ----------
echo [1/4] Proveryayu .venv...
if exist ".venv\Scripts\python.exe" (
    echo       OK
    goto :venv_ok
)
echo       Net .venv - zapuskayu install.bat
if not exist "install.bat" (
    echo       [!] install.bat ne najden ryadom - sborka nevozmozhna
    goto :fail
)
call "install.bat"
if errorlevel 1 goto :fail
if not exist ".venv\Scripts\python.exe" (
    echo       [!] .venv tak i ne poyavilsya
    goto :fail
)

:venv_ok
set "VPY=.venv\Scripts\python.exe"

REM ---------- 2. PyInstaller ----------
echo [2/4] Proveryayu PyInstaller...
"%VPY%" -c "import PyInstaller" >nul 2>&1
if not errorlevel 1 (
    echo       OK
    goto :pyi_ok
)
echo       Ustanavlivayu PyInstaller...
"%VPY%" -m pip install pyinstaller --quiet --disable-pip-version-check
if errorlevel 1 (
    echo       [!] Ne udalos postavit PyInstaller - proverte internet ili proxy
    goto :fail
)
"%VPY%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo       [!] PyInstaller postavilsya, no ne importiruetsya
    goto :fail
)
echo       OK

:pyi_ok

REM ---------- 3. Sborka ----------
echo [3/4] Sobirayu dva exe (minuta-dve)...
"%VPY%" -m PyInstaller vless2socks.spec --noconfirm
if errorlevel 1 (
    echo       [!] Sborka ne udalas - smotrite vyvod vyshe
    goto :fail
)
echo       OK

REM ---------- 4. xray-core ----------
REM Zashivat 65 MB v onefile-exe nelzya: raspakovka v temp pri kazhdom zapuske.
REM Pustoj bin\ - ne oshibka: programma skachaet xray sama pri pervom zapuske
REM profilya, kotoromu on nuzhen (reality / xtls / ws).
echo [4/4] xray-core ryadom s exe...
if exist "bin\xray.exe" (
    if not exist "dist\bin" mkdir "dist\bin"
    copy /y "bin\xray.exe" "dist\bin\" >nul
    if exist "bin\geoip.dat"   copy /y "bin\geoip.dat"   "dist\bin\" >nul
    if exist "bin\geosite.dat" copy /y "bin\geosite.dat" "dist\bin\" >nul
    echo       Skopirovan v dist\bin
) else (
    echo       bin\xray.exe net - programma skachaet ego sama pri pervom zapuske
)

echo.
echo ==================================================
echo    Gotovo. Soderzhimoe dist:
echo ==================================================
dir /b "dist"
echo.
echo    Zapuskat: dist\vless2socks.exe
echo    Konfigi sozdayutsya sami pri pervom zapuske.
echo ==================================================
echo.
pause
exit /b 0

:fail
echo.
echo Sborka prervana.
echo.
pause
exit /b 1
