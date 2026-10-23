@echo off
cd /d "%~dp0\..\.."
echo ========================================================
echo      Xshell 8 Setup ^& Proxy Integration Helper
echo ========================================================
echo.
echo 1. Download official NetSarang Xshell 8 installer
echo 2. Launch installer on Desktop
echo 3. Repair shortcuts (Desktop ^& Start Menu)
echo 4. Set SOCKS5 proxy to port 1030 (vless2socks)
echo 5. Create default SSH session for 192.168.1.138
echo 6. Exit
echo.
set /p opt="Select an option (1-6): "

if "%opt%"=="1" python -m setup_helpers.xshell.cli download
if "%opt%"=="2" python -m setup_helpers.xshell.cli launch
if "%opt%"=="3" python -m setup_helpers.xshell.cli fix-shortcuts
if "%opt%"=="4" python -m setup_helpers.xshell.cli set-proxy --port 1030
if "%opt%"=="5" python -m setup_helpers.xshell.cli add-session --name "192.168.1.138" --host "192.168.1.138" --user "mazitov" --password "password" --proxy "SOCKS5_1030"
if "%opt%"=="6" exit /b 0

pause
