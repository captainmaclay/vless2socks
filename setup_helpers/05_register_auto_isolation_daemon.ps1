# Step 5: Register autonomous boot containment daemon in Windows Task Scheduler
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Write-Host "=== [5/8] Registering WSL Autonomous Boot Containment Daemon ===" -ForegroundColor Cyan

$taskName = "WSL_AutoIsolation_Guard"
$daemonScript = "C:\proj\vless2socks\wsl-proxy-isolation\auto_isolate_daemon.ps1"

if (-not (Test-Path $daemonScript)) {
    Write-Error "Daemon script not found at $daemonScript"
}

# Unregister existing task if present
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[+] Unregistering previous task definition..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Register new logon task with highest privileges
Write-Host "[+] Registering Windows Scheduled Task: $taskName..." -ForegroundColor Yellow
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$daemonScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null

Write-Host "[SUCCESS] Task '$taskName' successfully registered! Will automatically enforce isolation on system boot." -ForegroundColor Green
