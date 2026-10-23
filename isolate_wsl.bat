@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

echo ======================================================================
echo    WSL2 Fail-Safe Network Isolation ^& Socket 1015 Setup
echo ======================================================================
echo.

set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo [!] .venv не найден. Убедитесь, что установлены зависимости проекта.
    pause
    exit /b 1
)

echo [1/3] Проверка доступности подсистемы WSL2...
wsl.exe -l -v >nul 2>&1
if errorlevel 1 (
    echo [!] WSL2 еще не активен. Возможно, требуется перезагрузка ПК
    echo     или установка дистрибутива через скрипты setup_helpers.
    echo.
    pause
    exit /b 1
)

echo [2/3] Проверка дистрибутива Ubuntu...
"%VPY%" wsl-proxy-isolation\cli.py check-wsl
wsl.exe -l -q | findstr /i "Ubuntu" >nul 2>&1
if errorlevel 1 (
    echo     Первичная инициализация Ubuntu 24.04 LTS...
    ubuntu2404.exe install --root
)

echo.
echo [3/3] Применение изоляции сетевого ядра на порт 1015 (nftables)...
"%VPY%" wsl-proxy-isolation\cli.py apply --port 1015
if errorlevel 1 (
    echo [!] Ошибка при применении изоляции.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo    Запуск комплексного аудита сетевой изоляции на сокет 1015...
echo ======================================================================
"%VPY%" wsl-proxy-isolation\cli.py test --port 1015

echo.
pause
exit /b 0
