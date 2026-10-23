@echo off
chcp 65001 >nul
title Claude Code CLI (Vendor C - Socket 1016 Secondary)

set "PY=python"
if exist "C:\proj\vless2socks\.venv\Scripts\python.exe" set "PY=C:\proj\vless2socks\.venv\Scripts\python.exe"

REM 1. Pre-launch IP Verification and Killswitch Protection
"%PY%" "C:\MyFiles\vless2socks\new_claude\verify_and_notify.py" --port 1016 --app "Vendor C CLI"
if errorlevel 1 (
    echo [BLOCKED] Launch aborted: Killswitch blocked traffic leak!
    timeout /t 5 >nul
    exit /b 1
)

REM 2. Routing strictly via socket 127.0.0.1:1016
set HTTP_PROXY=http://127.0.0.1:1016
set HTTPS_PROXY=http://127.0.0.1:1016
set ALL_PROXY=http://127.0.0.1:1016
set http_proxy=http://127.0.0.1:1016
set https_proxy=http://127.0.0.1:1016
set all_proxy=http://127.0.0.1:1016
set NO_PROXY=

REM 3. Isolated configuration directory for Profile 2 (Secondary)
set CLAUDE_CONFIG_DIR=C:\Users\Admin\.claude-secondary
set CLAUDE_SECURESTORAGE_CONFIG_DIR=C:\Users\Admin\.claude-secondary
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

REM 4. Sync profile status with vless2socks
"%PY%" "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1016 --quiet

cd /d "C:\Users\Admin\Downloads\Vendor_C_1016"
if not exist "C:\Users\Admin\Downloads\Vendor_C_1016" mkdir "C:\Users\Admin\Downloads\Vendor_C_1016"

"C:\Users\Admin\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe" --dangerously-skip-permissions %*

REM 5. Update profile status on exit
"%PY%" "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1016 --quiet
