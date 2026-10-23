# WSL2 Auto-Isolation Daemon & Boot Initializer
$logFile = "C:\proj\vless2socks\wsl-proxy-isolation\auto_isolate.log"
function Write-Log($msg) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] $msg" | Out-File $logFile -Append -Encoding utf8
}

Write-Log "=== WSL Auto-Isolation Guard Started ==="

# 1. Wait for WSL subsystem to become available (up to 120 seconds after Windows boot)
$attempts = 0
$wslReady = $false
while ($attempts -lt 40) {
    $p = Start-Process -FilePath "wsl.exe" -ArgumentList "--status" -NoNewWindow -PassThru -RedirectStandardOutput "$env:TEMP\wsl_stat_chk.txt" -RedirectStandardError "$env:TEMP\wsl_stat_err.txt"
    $p.WaitForExit()
    if (Test-Path "$env:TEMP\wsl_stat_chk.txt") {
        $content = [System.IO.File]::ReadAllText("$env:TEMP\wsl_stat_chk.txt", [System.Text.Encoding]::Unicode)
        if ($content -match "2" -or $content -match "Ubuntu" -or $content -match "Default" -or $content -match "Версия") {
            $wslReady = $true
            break
        }
    }
    Start-Sleep -Seconds 3
    $attempts++
}

if (-not $wslReady) {
    Write-Log "WSL service is not ready yet or awaiting initial PC reboot."
    exit 0
}

Write-Log "WSL service is active and responsive."

# 2. Check if Ubuntu is installed
$distroOut = & wsl.exe -l -q 2>&1 | Out-String
if ($distroOut -notmatch "Ubuntu") {
    Write-Log "Ubuntu not registered yet. Initializing Ubuntu 24.04 LTS..."
    $inst = Start-Process -FilePath "ubuntu2404.exe" -ArgumentList "install --root" -NoNewWindow -PassThru
    $inst.WaitForExit()
    Write-Log "Ubuntu initialization finished with exit code $($inst.ExitCode)."
}

# Ensure Ubuntu is set as default distro
$distroLines = (& wsl.exe -l -q 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_.Trim() -ne "" }
$uDistro = $distroLines | Where-Object { $_ -match "Ubuntu" } | Select-Object -First 1
if ($uDistro) {
    $uName = $uDistro.Trim().Replace("`0", "")
    Write-Log "Setting default WSL distribution to: $uName"
    & wsl.exe -s $uName
}

# 3. Apply isolation via python script
$python = "C:\proj\vless2socks\.venv\Scripts\python.exe"
$cli = "C:\proj\vless2socks\wsl-proxy-isolation\cli.py"

if (Test-Path $python) {
    Write-Log "Applying strict isolation to port 1015 (persistent in /etc/wsl.conf & /etc/nftables.conf)..."
    $applyOut = & $python $cli apply --port 1015 2>&1 | Out-String
    Write-Log "Apply output: $applyOut"

    Write-Log "Running isolation audit test..."
    $testOut = & $python $cli test --port 1015 2>&1 | Out-String
    Write-Log "Test audit result: $testOut"
} else {
    Write-Log "Python venv not found at $python"
}

Write-Log "=== WSL Auto-Isolation Guard Completed Successfully ==="
