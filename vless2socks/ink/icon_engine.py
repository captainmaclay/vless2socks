"""Engine for extracting raw PE icon layers and building multi-resolution ICO files."""

from __future__ import annotations

import io
import os
import struct
from pathlib import Path
from typing import List, Optional, Tuple, Union
from PIL import Image

from .colors import recolor_chrome_image, recolor_monochrome_hue

DEFAULT_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

DEFAULT_XSHELL_PATHS = [
    r"C:\Program Files (x86)\NetSarang\Xshell 8\Xshell.exe",
    r"C:\Program Files\NetSarang\Xshell 8\Xshell.exe",
]


def find_binary(candidates: List[str]) -> Optional[str]:
    """Finds the first existing executable path from candidates."""
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def extract_pe_icon_frames(exe_path: str, group_id: Optional[int] = None) -> List[Image.Image]:
    """Extracts all 32-bit icon frames directly from PE binary resources.

    This avoids Windows icon scaling bugs caused by saving flat PNGs with margins.
    """
    import pefile

    pe = pefile.PE(exe_path)
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
                if group_id is not None and grp_res.id != group_id:
                    continue
                data_entry = grp_res.directory.entries[0].data
                rva = data_entry.struct.OffsetToData
                size = data_entry.struct.Size
                group_icon_data = pe.get_data(rva, size)
                if group_id is not None or group_icon_data:
                    break

    if not group_icon_data:
        raise ValueError(f"No group icon resource found in {exe_path}")

    idReserved, idType, idCount = struct.unpack("<HHH", group_icon_data[:6])
    frames: List[Image.Image] = []

    for i in range(idCount):
        offset = 6 + i * 14
        (
            bWidth,
            bHeight,
            bColorCount,
            bReserved,
            wPlanes,
            wBitCount,
            dwBytesInRes,
            nID,
        ) = struct.unpack("<BBBBHHIH", group_icon_data[offset : offset + 14])

        if wBitCount != 32:
            continue

        raw_data = icon_entries.get(nID)
        if not raw_data:
            continue

        if raw_data.startswith(b"\x89PNG\r\n\x1a\n"):
            img = Image.open(io.BytesIO(raw_data)).convert("RGBA")
        else:
            ico_header = struct.pack(
                "<HHHBBBBHHII",
                0,
                1,
                1,
                bWidth,
                bHeight,
                bColorCount,
                bReserved,
                wPlanes,
                wBitCount,
                len(raw_data),
                22,
            )
            img = Image.open(io.BytesIO(ico_header + raw_data)).convert("RGBA")
        frames.append(img)

    frames.sort(key=lambda im: im.size[0], reverse=True)
    return frames


def save_multires_ico(frames: List[Image.Image], out_path: Union[str, Path]) -> str:
    """Saves a list of RGBA image frames into a multi-resolution Windows ICO file."""
    if not frames:
        raise ValueError("Cannot save empty frames list to ICO")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_frames = sorted(frames, key=lambda im: im.size[0], reverse=True)
    base = sorted_frames[0]
    append_list = sorted_frames[1:]
    sizes = [im.size for im in sorted_frames]

    base.save(str(out_path), format="ICO", sizes=sizes, append_images=append_list)
    return str(out_path)


def generate_chrome_icon(theme: str, out_ico_path: Union[str, Path], chrome_exe: Optional[str] = None) -> str:
    """Extracts Chrome frames, recolors them according to theme, and writes an ICO file."""
    exe = chrome_exe or find_binary(DEFAULT_CHROME_PATHS)
    if not exe:
        raise FileNotFoundError("Google Chrome executable not found on system")

    frames = extract_pe_icon_frames(exe)
    recolored = [recolor_chrome_image(f.copy(), theme) for f in frames]
    return save_multires_ico(recolored, out_ico_path)


def generate_app_icon(
    app_exe: str,
    out_ico_path: Union[str, Path],
    target_hue: float = 0.51,
    group_id: Optional[int] = None,
) -> str:
    """Extracts PE frames from any application, recolors dominant hue, and writes an ICO file."""
    if not os.path.isfile(app_exe):
        raise FileNotFoundError(f"Binary not found: {app_exe}")

    frames = extract_pe_icon_frames(app_exe, group_id=group_id)
    recolored = [recolor_monochrome_hue(f.copy(), target_hue=target_hue) for f in frames]
    return save_multires_ico(recolored, out_ico_path)
