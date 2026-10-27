@echo off
chcp 65001 >nul
:: Google Chrome via SOCKS5 socket 127.0.0.1:1099
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --proxy-server="socks5://127.0.0.1:1099" --user-data-dir="%LOCALAPPDATA%\Google\Chrome\Proxy1099" %*
