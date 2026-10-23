@echo off
chcp 65001 >nul
REM Silent launcher for Claude Desktop GUI Instance 2 (Port 1016)
start "" wscript.exe "C:\MyFiles\herdrCenter\run_claude_gui_2.vbs"
