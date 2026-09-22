@echo off
REM Sborka reliza: dva exe + xray-core ryadom s nimi.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [!] Ne najden .venv\Scripts\pyinstaller.exe
    echo     Ustanovite: .venv\Scripts\pip.exe install pyinstaller
    pause
    exit /b 1
)

echo == PyInstaller: vless2socks.exe + vless2socks-cli.exe ==
".venv\Scripts\pyinstaller.exe" vless2socks.spec --noconfirm
if errorlevel 1 (
    echo [!] Sborka ne udalas
    pause
    exit /b 1
)

REM xray-core i ego bazy dolzhny lezhat v dist\bin ryadom s exe:
REM zashivat 65 MB vnutr onefile-exe nelzya - raspakovka v temp pri kazhdom zapuske.
echo == Kopiruyu xray-core v dist\bin ==
if not exist "dist\bin" mkdir "dist\bin"
if exist "bin\xray.exe" (
    copy /y "bin\xray.exe" "dist\bin\" >nul
) else (
    echo [!] Net bin\xray.exe - snachala: python -m tools.get_xray
)
if exist "bin\geoip.dat"   copy /y "bin\geoip.dat"   "dist\bin\" >nul
if exist "bin\geosite.dat" copy /y "bin\geosite.dat" "dist\bin\" >nul

echo.
echo == Gotovo. Soderzhimoe dist: ==
dir /b "dist"
echo.
echo Zapuskat: dist\vless2socks.exe
echo Config/instances/settings sozdayutsya sami pri pervom zapuske.
pause
