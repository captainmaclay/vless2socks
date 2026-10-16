# ==============================================================================
# Windows Shortcut Generator for Isolated Claude Desktop Instances
# Creates Desktop & Proxy/ink shortcuts with multi-res colored icons
# ==============================================================================
param(
    [Parameter(Mandatory=$true)]
    [string]$InstanceId,

    [Parameter(Mandatory=$true)]
    [string]$ShortcutName,

    [Parameter(Mandatory=$true)]
    [string]$VbsPath,

    [Parameter(Mandatory=$true)]
    [string]$IconPath
)

$wsh = New-Object -ComObject WScript.Shell
$desktopDir = [Environment]::GetFolderPath('Desktop')
$proxyInkDir = "C:\MyFiles\Proxy\ink"
$workDir = "C:\MyFiles\herdrCenter"
$wscript = "$env:SystemRoot\System32\wscript.exe"
$arguments = "`"$VbsPath`""

if (!(Test-Path $proxyInkDir)) {
    New-Item -ItemType Directory -Path $proxyInkDir -Force | Out-Null
}

$targets = @(
    (Join-Path $desktopDir "$ShortcutName.lnk"),
    (Join-Path $proxyInkDir "$ShortcutName.lnk")
)

foreach ($target in $targets) {
    $lnk = $wsh.CreateShortcut($target)
    $lnk.TargetPath = $wscript
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $workDir
    $lnk.IconLocation = "$IconPath,0"
    $lnk.Description = "Claude Desktop GUI ($ShortcutName)"
    $lnk.Save()
    Write-Host "[OK] Created shortcut: $target"
}

# Refresh Windows Explorer icon cache
try {
    Start-Process "ie4uinit.exe" -ArgumentList "-show" -Wait -NoNewWindow -ErrorAction SilentlyContinue
} catch {}
