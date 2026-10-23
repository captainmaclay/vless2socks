' ==============================================================================
' Silent Launcher for Vendor C Desktop GUI (Instance {INSTANCE_ID})
' Pre-launch verification with Mac UI toast + WSLg health check + Silent WSL launch
' ==============================================================================
Set WshShell = CreateObject("WScript.Shell")

Const DISTRO = "{DISTRO}"
Const WSL_USER = "{WSL_USER}"
Const MAX_TRIES = 4
Const RETRY_MS = 1000

' 1. Pre-launch IP check against vless2socks with floating Mac UI toast notification
ret = WshShell.Run("pythonw ""C:\MyFiles\vless2socks\new_claude\verify_and_notify.py"" --port {SOCKS_PORT} --app ""Vendor C GUI""", 0, True)
If ret <> 0 Then WScript.Quit 1

' 2. WSLg health check (retried while wsl.exe itself is not ready):
'    0 = OK, 10 = COPY MODE and no GUI running, 11 = COPY MODE but GUI running.
'    COPY MODE means broken WSLg shared memory: windows are created but stay blank,
'    only a full `wsl --shutdown` restores it.
health = -1
For i = 1 To MAX_TRIES
    health = WshShell.Run("wsl.exe -d " & DISTRO & " -u " & WSL_USER & " bash /usr/local/bin/wslg-health", 0, True)
    If health = 0 Or health = 10 Or health = 11 Then Exit For
    WScript.Sleep RETRY_MS
Next

If health = 10 Then
    WshShell.Run "wsl.exe --shutdown", 0, True
    WScript.Sleep 2000
End If

' 3. Launch (the WSL launcher itself retries Electron start up to 4 times and holds a lockfile)
WshShell.Run "wsl.exe -d " & DISTRO & " -u " & WSL_USER & " bash -lc ""/usr/local/bin/claude-gui-{INSTANCE_ID}""", 0, False
