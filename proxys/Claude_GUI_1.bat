@echo off
chcp 65001 >nul
REM Silent launcher for Claude Desktop GUI Instance 1 (Port 1015)
start "" wscript.exe "C:\MyFiles\herdrCenter\run_claude_gui_1.vbs"
