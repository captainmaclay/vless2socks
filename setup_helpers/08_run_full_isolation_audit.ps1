# Step 8: Full Isolation and Security Audit Test
[CmdletBinding()]
param(
    [int]$Port = 1015
)

$ErrorActionPreference = "Stop"
Write-Host "=== [8/8] Executing Full Zero-Leaks Network Isolation Audit ===" -ForegroundColor Cyan

$python = "C:\proj\vless2socks\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python.exe" }

$cli = "C:\proj\vless2socks\wsl-proxy-isolation\cli.py"

$output = & $python $cli test --port $Port 2>&1 | Tee-Object -Variable auditOut

if ($output -match "ISOLATED") {
    Write-Host "`n[VERDICT: 100% ISOLATED] Congratulations! The system network perimeter is fully airtight." -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n[VERDICT: PARTIAL OR FAILED] Issues detected during isolation audit. Review logs above." -ForegroundColor Red
    exit 1
}
