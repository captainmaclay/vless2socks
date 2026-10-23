# Step 3: Register Ubuntu 24.04 and install Linux kernel filter utilities (nftables, iptables, curl)
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Write-Host "=== [3/8] Initializing Ubuntu 24.04 & Installing Kernel Netfilter Packages ===" -ForegroundColor Cyan

# 1. Check if Ubuntu is registered in WSL
$distroList = & wsl.exe -l -v 2>&1 | Out-String
if ($distroList -notmatch "Ubuntu") {
    Write-Host "[+] Ubuntu is not registered yet. Initializing Ubuntu rootfs..." -ForegroundColor Yellow
    $appxExe = "ubuntu2404.exe"
    $proc = Start-Process -FilePath $appxExe -ArgumentList "install --root" -NoNewWindow -PassThru -Wait
    if ($proc.ExitCode -ne 0) {
        Write-Error "Failed to initialize Ubuntu with exit code $($proc.ExitCode)."
    }
    Write-Host "[+] Ubuntu rootfs initialization completed." -ForegroundColor Green
}

# 2. Ensure default distro is Ubuntu-24.04
Write-Host "[+] Setting Ubuntu-24.04 as default WSL distribution..." -ForegroundColor Yellow
& wsl.exe --set-default Ubuntu-24.04 2>$null

# 3. Non-interactive installation of netfilter packages inside Ubuntu
Write-Host "[+] Installing nftables, iptables, curl inside Ubuntu 24.04..." -ForegroundColor Yellow
$installScript = @"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq -o Dpkg::Options::="--force-confold" nftables iptables curl
"@

$encodedScript = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($installScript))
$cmd = "echo $encodedScript | base64 -d | bash"

& wsl.exe -d Ubuntu-24.04 -u root -e bash -c $cmd
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install kernel filter packages inside Ubuntu."
}

# 4. Verify tools exist
$chk = & wsl.exe -d Ubuntu-24.04 -u root -e bash -c "which nft && which iptables && which curl" 2>&1 | Out-String
if ($chk -match "nft" -and $chk -match "iptables" -and $chk -match "curl") {
    Write-Host "[SUCCESS] Linux kernel filter packages successfully installed and verified in Ubuntu 24.04!" -ForegroundColor Green
} else {
    Write-Error "Verification failed. Output: $chk"
}
