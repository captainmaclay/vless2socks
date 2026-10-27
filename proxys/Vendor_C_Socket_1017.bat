@echo off
chcp 65001 >nul
title Vendor C Code (Instance 3 - Socket 1017)

REM 1. Pre-launch IP Verification with Mac UI floating notification (Research Sandbox)
python "C:\MyFiles\vless2socks\new_claude\verify_and_notify.py" --port 1017 --app "Vendor C CLI"
if errorlevel 1 (
    echo [BLOCKED] IP verification failed. Launch aborted to prevent leak!
    timeout /t 3 >nul
    exit /b 1
)

REM 2. Vendor C Code CLI strictly via socket 127.0.0.1:1017
set HTTP_PROXY=http://127.0.0.1:1017
set HTTPS_PROXY=http://127.0.0.1:1017
set ALL_PROXY=http://127.0.0.1:1017
set http_proxy=http://127.0.0.1:1017
set https_proxy=http://127.0.0.1:1017
set all_proxy=http://127.0.0.1:1017
set NO_PROXY=

REM Isolated configuration directory for profile 3
set CLAUDE_CONFIG_DIR=C:\Users\Admin\.claude-3
set CLAUDE_SECURESTORAGE_CONFIG_DIR=C:\Users\Admin\.claude-3
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

REM 3. Synchronize Vendor C profile metadata with vless2socks
python "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1017 --quiet

cd /d "C:\Users\Admin\Downloads\Vendor_C_1017"
"C:\Users\Admin\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe" --dangerously-skip-permissions %*

REM 4. Update status on exit in case session state changed
python "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1017 --quiet
