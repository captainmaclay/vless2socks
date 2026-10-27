@echo off
chcp 65001 >nul
:: Google Chrome via SOCKS5 socket 127.0.0.1:1105
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --proxy-server="socks5://127.0.0.1:1105" --user-data-dir="%LOCALAPPDATA%\Google\Chrome\Proxy1105" %*
