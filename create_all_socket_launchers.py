import colorsys
import os
import io
import struct
import subprocess
from PIL import Image

def recolor_image(img, theme):
    img = img.convert("RGBA")
    pixels = img.load()
    w, h = img.size

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < 10:
                continue

            nr, ng, nb = r / 255.0, g / 255.0, b / 255.0
            h_val, s_val, v_val = colorsys.rgb_to_hsv(nr, ng, nb)

            # Preserve white separator ring
            if s_val < 0.15 and v_val > 0.80:
                continue
            if s_val < 0.12 and theme not in ('black', 'gray'):
                continue

            is_yellow = 0.10 <= h_val <= 0.22
            is_green = 0.23 <= h_val <= 0.45
            is_blue = 0.50 <= h_val <= 0.70

            if theme == 'gold': # Golden Yellow (System Proxy 1015)
                if is_blue:
                    new_h, new_s, new_v = 0.12, 0.95, 0.65 # Deep Amber/Gold Core
                elif is_yellow:
                    new_h, new_s, new_v = 0.14, 0.90, 0.98 # Bright Gold
                elif is_green:
                    new_h, new_s, new_v = 0.09, 0.95, 0.70 # Dark Bronze/Orange
                else:
                    new_h, new_s, new_v = 0.13, 0.95, 0.90 # Rich Golden yellow

            elif theme == 'blue': # Electric / Royal Blue (WorProxy 1030)
                if is_blue:
                    new_h, new_s, new_v = 0.65, 0.95, 0.55 # Deep Navy Core
                elif is_yellow:
                    new_h, new_s, new_v = 0.57, 0.85, 0.98 # Sky blue
                elif is_green:
                    new_h, new_s, new_v = 0.63, 0.95, 0.65 # Indigo
                else:
                    new_h, new_s, new_v = 0.60, 0.95, 0.90 # Royal Blue

            elif theme == 'purple': # Purple / Violet (Russia-0.1 1020)
                if is_blue:
                    new_h, new_s, new_v = 0.77, 0.95, 0.55 # Deep violet core
                elif is_yellow:
                    new_h, new_s, new_v = 0.74, 0.85, 0.98 # Lilac/violet
                elif is_green:
                    new_h, new_s, new_v = 0.80, 0.95, 0.65 # Dark purple
                else:
                    new_h, new_s, new_v = 0.76, 0.95, 0.90 # Vivid purple

            elif theme == 'orange': # Orange / Amber (02-Russia 1021)
                if is_blue:
                    new_h, new_s, new_v = 0.06, 0.95, 0.60 # Deep burnt orange core
                elif is_yellow:
                    new_h, new_s, new_v = 0.10, 0.90, 0.98 # Amber
                elif is_green:
                    new_h, new_s, new_v = 0.04, 0.95, 0.65 # Dark Rust
                else:
                    new_h, new_s, new_v = 0.07, 0.95, 0.95 # Vivid Orange

            elif theme == 'black': # Black / Charcoal (1081)
                if is_blue:
                    new_h, new_s, new_v = 0.0, 0.0, 0.12
                elif is_yellow:
                    new_h, new_s, new_v = 0.0, 0.0, 0.55
                elif is_green:
                    new_h, new_s, new_v = 0.0, 0.0, 0.35
                else:
                    new_h, new_s, new_v = 0.0, 0.0, 0.75

            elif theme == 'green': # Green / Emerald (1082)
                if is_blue:
                    new_h, new_s, new_v = 0.38, 0.95, 0.55
                elif is_yellow:
                    new_h, new_s, new_v = 0.25, 0.95, 0.95
                elif is_green:
                    new_h, new_s, new_v = 0.35, 0.95, 0.65
                else:
                    new_h, new_s, new_v = 0.30, 0.95, 0.85

            elif theme == 'red': # Red / Ruby (1083)
                if is_blue:
                    new_h, new_s, new_v = 0.99, 0.95, 0.65
                elif is_yellow:
                    new_h, new_s, new_v = 0.04, 0.95, 0.95
                elif is_green:
                    new_h, new_s, new_v = 0.97, 0.95, 0.65
                else:
                    new_h, new_s, new_v = 0.00, 0.95, 0.90

            elif theme == 'cyan': # Cyan / Turquoise (1084)
                if is_blue:
                    new_h, new_s, new_v = 0.52, 0.95, 0.60
                elif is_yellow:
                    new_h, new_s, new_v = 0.48, 0.90, 0.95
                elif is_green:
                    new_h, new_s, new_v = 0.54, 0.95, 0.65
                else:
                    new_h, new_s, new_v = 0.50, 0.95, 0.90

            elif theme == 'pink': # Pink / Rose (1085)
                if is_blue:
                    new_h, new_s, new_v = 0.88, 0.90, 0.60
                elif is_yellow:
                    new_h, new_s, new_v = 0.94, 0.85, 0.98
                elif is_green:
                    new_h, new_s, new_v = 0.86, 0.95, 0.65
                else:
                    new_h, new_s, new_v = 0.91, 0.95, 0.92

            else:
                continue

            fr, fg, fb = colorsys.hsv_to_rgb(new_h, new_s, new_v)
            pixels[x, y] = (int(fr * 255), int(fg * 255), int(fb * 255), a)

    return img

def extract_original_frames():
    import pefile
    chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    pe = pefile.PE(chrome_exe)

    rt_icon_type = 3
    rt_group_icon_type = 14

    icon_entries = {}
    group_icon_data = None

    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if entry.id == rt_icon_type:
            for icon_res in entry.directory.entries:
                res_id = icon_res.id
                data_entry = icon_res.directory.entries[0].data
                rva = data_entry.struct.OffsetToData
                size = data_entry.struct.Size
                icon_entries[res_id] = pe.get_data(rva, size)
        elif entry.id == rt_group_icon_type:
            for grp_res in entry.directory.entries:
                data_entry = grp_res.directory.entries[0].data
                rva = data_entry.struct.OffsetToData
                size = data_entry.struct.Size
                group_icon_data = pe.get_data(rva, size)
                break

    idReserved, idType, idCount = struct.unpack('<HHH', group_icon_data[:6])
    frames = []
    for i in range(idCount):
        offset = 6 + i * 14
        bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount, dwBytesInRes, nID = struct.unpack(
            '<BBBBHHIH', group_icon_data[offset:offset+14]
        )
        if wBitCount != 32:
            continue
        raw_data = icon_entries[nID]
        if raw_data.startswith(b'\x89PNG\r\n\x1a\n'):
            img = Image.open(io.BytesIO(raw_data)).convert('RGBA')
        else:
            ico_header = struct.pack('<HHHBBBBHHII', 0, 1, 1, bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount, len(raw_data), 22)
            img = Image.open(io.BytesIO(ico_header + raw_data)).convert('RGBA')
        frames.append(img)
    return frames

def build_ico(frames, theme, out_path):
    recolored = [recolor_image(f.copy(), theme) for f in frames]
    recolored.sort(key=lambda im: im.size[0], reverse=True)
    base = recolored[0]
    append_list = recolored[1:]
    sizes = [im.size for im in recolored]
    base.save(out_path, format='ICO', sizes=sizes, append_images=append_list)
    print(f"Saved {out_path}")

def main():
    proxy_dir = r"C:\MyFiles\Proxy"
    ico_dir = os.path.join(proxy_dir, "ico")
    os.makedirs(ico_dir, exist_ok=True)

    orig_frames = extract_original_frames()
    print("Extracted original frames.")

    # List of all 9 instances: (Port, Name/Label, ProfileDir, ColorTheme, IcoFileName)
    all_proxies = [
        (1015, "Chrome Socket 1015", "Proxy1015", "gold",   "chrome_gold.ico"),
        (1030, "Chrome Socket 1030", "Proxy1030", "blue",   "chrome_blue.ico"),
        (1020, "Chrome Socket 1020", "Proxy1020", "purple", "chrome_purple.ico"),
        (1021, "Chrome Socket 1021", "Proxy1021", "orange", "chrome_orange.ico"),
        (1081, "Chrome Socket 1081", "Proxy1081", "black",  "chrome_black.ico"),
        (1082, "Chrome Socket 1082", "Proxy1082", "green",  "chrome_green.ico"),
        (1083, "Chrome Socket 1083", "Proxy1083", "red",    "chrome_red.ico"),
        (1084, "Chrome Socket 1084", "Proxy1084", "cyan",   "chrome_cyan.ico"),
        (1085, "Chrome Socket 1085", "Proxy1085", "pink",   "chrome_pink.ico"),
    ]

    # Generate or refresh all icons
    for port, label, profile, theme, ico_file in all_proxies:
        ico_path = os.path.join(ico_dir, ico_file)
        build_ico(orig_frames, theme, ico_path)

    # Generate .bat files in proxy_dir
    for port, label, profile, theme, ico_file in all_proxies:
        bat_name = f"Chrome_Socket_{port}.bat"
        bat_path = os.path.join(proxy_dir, bat_name)
        bat_content = f"""@echo off
chcp 65001 >nul
:: Google Chrome через SOCKS5 сокет 127.0.0.1:{port}
start "" "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --proxy-server="socks5://127.0.0.1:{port}" --user-data-dir="%LOCALAPPDATA%\\Google\\Chrome\\{profile}" %*
"""
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        print(f"Created batch file: {bat_name}")

    # Remove obsolete shortcuts and bat files if needed
    for f in os.listdir(proxy_dir):
        if f.startswith("Chrome_#") and f.endswith(".bat"):
            try:
                os.remove(os.path.join(proxy_dir, f))
                print(f"Cleaned up legacy bat: {f}")
            except Exception:
                pass

    # Create/update shortcuts in proxy_dir
    ps_lines = [
        "$wsh = New-Object -ComObject WScript.Shell",
        f"$proxyDir = '{proxy_dir}'",
        f"$inkDir = [System.IO.Path]::Combine($proxyDir, 'ink')",
        f"$icoDir = '{ico_dir}'",
        f"$desktop = [Environment]::GetFolderPath('Desktop')"
    ]

    for port, label, profile, theme, ico_file in all_proxies:
        bat_name = f"Chrome_Socket_{port}.bat"
        ps_lines.append(f"$lnk = [System.IO.Path]::Combine($inkDir, '{label}.lnk')")
        ps_lines.append(f"$bat = [System.IO.Path]::Combine($proxyDir, '{bat_name}')")
        ps_lines.append(f"$ico = [System.IO.Path]::Combine($icoDir, '{ico_file}')")
        ps_lines.append(f"$sc = $wsh.CreateShortcut($lnk)")
        ps_lines.append(f"$sc.TargetPath = $bat")
        ps_lines.append(f"$sc.WorkingDirectory = $proxyDir")
        ps_lines.append(f"$sc.IconLocation = \"$ico,0\"")
        ps_lines.append(f"$sc.Save()")
        ps_lines.append(f"Write-Output \"Created shortcut: $lnk with icon {ico_file}\"")

    # Update Desktop ChromeSocks3 to Socket 1083 (Red)
    ps_lines.append(f"$deskLnk = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'ChromeSocks3.lnk')")
    ps_lines.append(f"$redIco = [System.IO.Path]::Combine($icoDir, 'chrome_red.ico')")
    ps_lines.append(f"if (Test-Path $deskLnk) {{")
    ps_lines.append(f"    $scDesk = $wsh.CreateShortcut($deskLnk)")
    ps_lines.append(f"    $scDesk.IconLocation = \"$redIco,0\"")
    ps_lines.append(f"    $scDesk.Save()")
    ps_lines.append(f"}}")

    ps_script_path = os.path.join(ico_dir, "create_all_shortcuts.ps1")
    with open(ps_script_path, "w", encoding="utf-8-sig") as f:
        f.write("\r\n".join(ps_lines))

    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script_path], capture_output=True, text=True)
    print("PS result:\n", res.stdout)
    if res.stderr:
        print("PS errors:\n", res.stderr)

    try:
        os.remove(ps_script_path)
    except Exception:
        pass

    subprocess.run(["ie4uinit.exe", "-show"])
    # Restart explorer cleanly
    subprocess.run(["powershell", "-Command", "Stop-Process -Name explorer -Force; Start-Sleep -Seconds 1; Start-Process explorer"])
    print("All batch files, icons, and shortcuts successfully configured and refreshed!")

if __name__ == "__main__":
    main()
