# Step 6: Setup Vendor C / Claude Code CLI isolated profiles with Killswitch (Ports 1015, 1016 and 1017)
# -BatOnly: only regenerate the .bat launchers (C:\MyFiles\Proxy + proxys\ copies); no npm, shortcuts, skills or sync
[CmdletBinding()]
param(
    [switch]$BatOnly
)

$ErrorActionPreference = "Stop"
Write-Host "=== [6/8] Configuring Vendor C Claude Code CLI Environments (Ports 1015, 1016 & 1017) ===" -ForegroundColor Cyan

# 1. Verify Node.js and claude CLI
$claudeBin = Join-Path $env:APPDATA "npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
if (-not $BatOnly -and -not (Test-Path $claudeBin)) {
    Write-Host "[+] Installing @anthropic-ai/claude-code globally via npm..." -ForegroundColor Yellow
    npm install -g @anthropic-ai/claude-code
}

$python = "C:\proj\vless2socks\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python.exe" }

$proxyDir = "C:\MyFiles\Proxy"
$desktopDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
New-Item -ItemType Directory -Path "$proxyDir\ink" -Force | Out-Null
New-Item -ItemType Directory -Path "$proxyDir\ico" -Force | Out-Null

$profiles = @(
    @{
        Port = 1015
        Name = "Claude Code 1015"
        CliAlias = "CLI_1015"
        ConfigDir = ".claude-1"
        Title = "Claude Code CLI (Vendor C - Socket 1015)"
        WorkDir = "Vendor_C_1015"
        Color = "gold"
    },
    @{
        Port = 1016
        Name = "Claude Code 1016"
        CliAlias = "CLI_1016"
        ConfigDir = ".claude-2"
        Title = "Claude Code CLI (Vendor C - Socket 1016 Profile 2)"
        WorkDir = "Vendor_C_1016"
        Color = "gold"
    },
    @{
        Port = 1017
        Name = "Claude Code 1017"
        CliAlias = "CLI_1017"
        ConfigDir = ".claude-3"
        Title = "Claude Code CLI (Vendor C - Socket 1017 Profile 3)"
        WorkDir = "Vendor_C_1017"
        Color = "gold"
    }
)

# Skills repository: add_profile.py registers a profile in claude_homes, deploys user skills and adds the update-check hook
$addProfileTool = "C:\myFiles\mainSkill-claude\tools\add_profile.py"

$wsh = New-Object -ComObject WScript.Shell

foreach ($p in $profiles) {
    $port = $p.Port
    $configPath = Join-Path $env:USERPROFILE $p.ConfigDir
    $workPath = Join-Path $env:USERPROFILE "Downloads\$($p.WorkDir)"
    New-Item -ItemType Directory -Path $configPath -Force | Out-Null
    New-Item -ItemType Directory -Path $workPath -Force | Out-Null

    $batPath = Join-Path $proxyDir "Vendor_C_Socket_$port.bat"
    $legacyBatPath = Join-Path $proxyDir "Claude_Socket_$port.bat"

    # Deploy launcher batch file with Killswitch
    $batContent = @"
@echo off
chcp 65001 >nul
title $($p.Title)

set "PY=python"
if exist "C:\proj\vless2socks\.venv\Scripts\python.exe" set "PY=C:\proj\vless2socks\.venv\Scripts\python.exe"

REM 1. Pre-launch IP Verification and Killswitch Protection
"%PY%" "C:\MyFiles\vless2socks\new_claude\verify_and_notify.py" --port $port --app "Vendor C CLI"
if errorlevel 1 (
    echo [BLOCKED] Launch aborted: Killswitch blocked traffic leak!
    timeout /t 3 >nul
    exit /b 1
)

REM 2. Routing strictly via socket 127.0.0.1:$port
set HTTP_PROXY=http://127.0.0.1:$port
set HTTPS_PROXY=http://127.0.0.1:$port
set ALL_PROXY=http://127.0.0.1:$port
set http_proxy=http://127.0.0.1:$port
set https_proxy=http://127.0.0.1:$port
set all_proxy=http://127.0.0.1:$port
set NO_PROXY=

REM 3. Isolated configuration directory
set CLAUDE_CONFIG_DIR=$configPath
set CLAUDE_SECURESTORAGE_CONFIG_DIR=$configPath
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

REM 4. Sync profile status with vless2socks
"%PY%" "C:\MyFiles\vless2socks\vendor_c_sync.py" --port $port --quiet

if not exist "$workPath" mkdir "$workPath"
cd /d "$workPath"

"$claudeBin" --dangerously-skip-permissions %*

REM 5. Update profile status on exit
"%PY%" "C:\MyFiles\vless2socks\vendor_c_sync.py" --port $port --quiet
"@

    # cmd.exe needs CRLF and no BOM (a BOM breaks the first line "@echo off")
    $batContent = ($batContent -replace "`r?`n", "`r`n") + "`r`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($batPath, $batContent, $utf8NoBom)
    [System.IO.File]::WriteAllText($legacyBatPath, $batContent, $utf8NoBom)
    Copy-Item $batPath, $legacyBatPath "C:\proj\vless2socks\proxys\" -Force -ErrorAction SilentlyContinue

    Write-Host "[+] Deployed batch launchers for Port $port." -ForegroundColor Yellow
    if ($BatOnly) { continue }

    # Shortcuts
    $icoPath = Join-Path $proxyDir "ico\claude_$($p.Color).ico"
    $shortcutTargets = @(
        (Join-Path $desktopDir "$($p.Name).lnk"),
        (Join-Path $desktopDir "$($p.CliAlias).lnk"),
        (Join-Path $proxyDir "ink\$($p.Name).lnk"),
        (Join-Path $proxyDir "ink\$($p.CliAlias).lnk"),
        (Join-Path "C:\proj\vless2socks\proxys\ink" "$($p.Name).lnk"),
        (Join-Path "C:\proj\vless2socks\proxys\ink" "$($p.CliAlias).lnk")
    )

    foreach ($sc in $shortcutTargets) {
        $parent = Split-Path $sc
        if (!(Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        $lnk = $wsh.CreateShortcut($sc)
        $lnk.TargetPath = "C:\Windows\System32\cmd.exe"
        $lnk.Arguments = "/k `"$batPath`""
        $lnk.WorkingDirectory = $proxyDir
        if (Test-Path $icoPath) { $lnk.IconLocation = "$icoPath,0" }
        $lnk.Description = "$($p.Title)"
        $lnk.Save()
    }

    # Connect skills (same as new_claude/add_vendor_c_cli_instance.py setup_profile_skills)
    if (Test-Path $addProfileTool) {
        & $python $addProfileTool $configPath
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[+] Skills connected: $configPath\skills" -ForegroundColor Yellow
        } else {
            Write-Host "[!] add_profile.py failed with code $LASTEXITCODE - skills of $configPath are not set up" -ForegroundColor Red
        }
    } else {
        Write-Host "[*] Skills not connected: $addProfileTool not found (clone mainSkill-claude, then run it with $configPath)" -ForegroundColor DarkYellow
    }

    # Sync profile status
    & $python "C:\proj\vless2socks\vendor_c_sync.py" --port $port --quiet
}

if ($BatOnly) {
    Write-Host "[SUCCESS] Batch launchers 1015, 1016 & 1017 regenerated (shortcuts, skills and sync skipped)." -ForegroundColor Green
    return
}
Write-Host "[SUCCESS] Vendor C Claude Code CLI profiles 1015, 1016 & 1017 successfully deployed and isolated!" -ForegroundColor Green
