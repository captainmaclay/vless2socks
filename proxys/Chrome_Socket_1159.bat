@echo off
chcp 65001 >nul
:: Google Chrome via SOCKS5 socket 127.0.0.1:1159
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --proxy-server="socks5://127.0.0.1:1159" --user-data-dir="%LOCALAPPDATA%\Google\Chrome\Proxy1159" %*
