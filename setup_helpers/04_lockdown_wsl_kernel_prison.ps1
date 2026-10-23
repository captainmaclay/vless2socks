# Step 4: Apply strict Linux kernel network prison to port 1015 (Herdr Standard)
[CmdletBinding()]
param(
    [int]$Port = 1015
)

$ErrorActionPreference = "Stop"
Write-Host "=== [4/8] Applying Linux Kernel Network Prison to Port $Port ===" -ForegroundColor Cyan

$python = "C:\proj\vless2socks\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python.exe"
}
$cli = "C:\proj\vless2socks\wsl-proxy-isolation\cli.py"

Write-Host "[+] Invoking isolation generator: $cli apply --port $Port..." -ForegroundColor Yellow
& $python $cli apply --port $Port

# Verify nftables rules in kernel
$tableChk = & wsl.exe -d Ubuntu-24.04 -u root -e bash -c "nft list table inet herdr_filter 2>/dev/null" 2>&1 | Out-String
if ($tableChk -match "chain output" -and $tableChk -match "reject") {
    Write-Host "[SUCCESS] Linux Netfilter table 'herdr_filter' successfully active in kernel!" -ForegroundColor Green
} else {
    Write-Error "nftables rule verification failed. Table output: $tableChk"
}

# Verify profile env
$envChk = & wsl.exe -d Ubuntu-24.04 -e bash -c "cat /etc/profile.d/herdr_claude_env.sh 2>/dev/null" 2>&1 | Out-String
if ($envChk -match "socks5h://127.0.0.1:$Port") {
    Write-Host "[SUCCESS] /etc/profile.d/herdr_claude_env.sh contains socks5h://127.0.0.1:$Port (Remote DNS delegated)!" -ForegroundColor Green
} else {
    Write-Error "Profile env verification failed. Output: $envChk"
}
