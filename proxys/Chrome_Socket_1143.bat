@echo off
chcp 65001 >nul
:: Google Chrome via SOCKS5 socket 127.0.0.1:1143
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --proxy-server="socks5://127.0.0.1:1143" --user-data-dir="%LOCALAPPDATA%\Google\Chrome\Proxy1143" %*
