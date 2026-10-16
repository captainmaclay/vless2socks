' ==============================================================================
' Silent Launcher for Vendor C Desktop GUI (Instance {INSTANCE_ID})
' Pre-launch verification with Mac UI toast + Silent WSL launch
' ==============================================================================
Set WshShell = CreateObject("WScript.Shell")

' 1. Pre-launch IP check against vless2socks with floating Mac UI toast notification
ret = WshShell.Run("pythonw ""C:\MyFiles\vless2socks\new_claude\verify_and_notify.py"" --port {SOCKS_PORT} --app ""Vendor C GUI""", 0, True)

' 2. Launch only if IP verification succeeded
If ret = 0 Then
    WshShell.Run "wsl.exe -d {DISTRO} bash -lc ""/usr/local/bin/claude-gui-{INSTANCE_ID}""", 0, False
End If
