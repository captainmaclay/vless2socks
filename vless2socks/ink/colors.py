"""Palette definitions and color transformation algorithms for custom application icons."""

from __future__ import annotations

import colorsys
from typing import Callable, Tuple
from PIL import Image

# Supported standard themes and their visual metadata
THEMES = {
    "gold": {
        "name": "Gold / Amber",
        "description": "Золотисто-янтарный",
        "core_hsv": (0.12, 0.95, 0.65),
        "bright_hsv": (0.14, 0.90, 0.98),
        "dark_hsv": (0.09, 0.95, 0.70),
        "main_hsv": (0.13, 0.95, 0.90),
    },
    "blue": {
        "name": "Royal Blue",
        "description": "Ярко-синий (сапфировый)",
        "core_hsv": (0.65, 0.95, 0.55),
        "bright_hsv": (0.57, 0.85, 0.98),
        "dark_hsv": (0.63, 0.95, 0.65),
        "main_hsv": (0.60, 0.95, 0.90),
    },
    "purple": {
        "name": "Purple / Violet",
        "description": "Фиолетовый",
        "core_hsv": (0.77, 0.95, 0.55),
        "bright_hsv": (0.74, 0.85, 0.98),
        "dark_hsv": (0.80, 0.95, 0.65),
        "main_hsv": (0.76, 0.95, 0.90),
    },
    "orange": {
        "name": "Vivid Orange",
        "description": "Оранжевый",
        "core_hsv": (0.06, 0.95, 0.60),
        "bright_hsv": (0.10, 0.90, 0.98),
        "dark_hsv": (0.04, 0.95, 0.65),
        "main_hsv": (0.07, 0.95, 0.95),
    },
    "black": {
        "name": "Charcoal Black",
        "description": "Черный / Графитовый",
        "core_hsv": (0.0, 0.0, 0.12),
        "bright_hsv": (0.0, 0.0, 0.55),
        "dark_hsv": (0.0, 0.0, 0.35),
        "main_hsv": (0.0, 0.0, 0.75),
    },
    "green": {
        "name": "Emerald Green",
        "description": "Изумрудно-зеленый",
        "core_hsv": (0.38, 0.95, 0.55),
        "bright_hsv": (0.25, 0.95, 0.95),
        "dark_hsv": (0.35, 0.95, 0.65),
        "main_hsv": (0.30, 0.95, 0.85),
    },
    "red": {
        "name": "Ruby Red",
        "description": "Рубиново-красный",
        "core_hsv": (0.99, 0.95, 0.65),
        "bright_hsv": (0.04, 0.95, 0.95),
        "dark_hsv": (0.97, 0.95, 0.65),
        "main_hsv": (0.00, 0.95, 0.90),
    },
    "cyan": {
        "name": "Turquoise / Cyan",
        "description": "Бирюзовый",
        "core_hsv": (0.52, 0.95, 0.60),
        "bright_hsv": (0.48, 0.90, 0.95),
        "dark_hsv": (0.54, 0.95, 0.65),
        "main_hsv": (0.50, 0.95, 0.90),
    },
    "pink": {
        "name": "Rose Pink",
        "description": "Розовый",
        "core_hsv": (0.88, 0.90, 0.60),
        "bright_hsv": (0.94, 0.85, 0.98),
        "dark_hsv": (0.86, 0.95, 0.65),
        "main_hsv": (0.91, 0.95, 0.92),
    },
}


def recolor_chrome_image(img: Image.Image, theme: str) -> Image.Image:
    """Recolors a Chrome icon frame preserving the white separator ring."""
    theme = theme.lower()
    if theme not in THEMES:
        return img.copy()

    t_data = THEMES[theme]
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

            # Preserve the white circular separator ring
            if s_val < 0.15 and v_val > 0.80:
                continue
            if s_val < 0.12 and theme != "black":
                continue

            is_yellow = 0.10 <= h_val <= 0.22
            is_green = 0.23 <= h_val <= 0.45
            is_blue = 0.50 <= h_val <= 0.70

            if is_blue:
                new_h, new_s, new_v = t_data["core_hsv"]
            elif is_yellow:
                new_h, new_s, new_v = t_data["bright_hsv"]
            elif is_green:
                new_h, new_s, new_v = t_data["dark_hsv"]
            else:
                new_h, new_s, new_v = t_data["main_hsv"]

            fr, fg, fb = colorsys.hsv_to_rgb(new_h, new_s, new_v)
            pixels[x, y] = (int(fr * 255), int(fg * 255), int(fb * 255), a)

    return img


def recolor_monochrome_hue(
    img: Image.Image,
    target_hue: float = 0.51,
    min_saturation: float = 0.15,
) -> Image.Image:
    """Recolors arbitrary application icons (e.g., Xshell, PuTTY, etc.) to target hue."""
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

            # Only recolor colored parts, leave neutral/white glyphs alone
            if s_val >= min_saturation:
                new_h = target_hue
                new_s = min(1.0, s_val * 1.05)
                new_v = v_val
                fr, fg, fb = colorsys.hsv_to_rgb(new_h, new_s, new_v)
                pixels[x, y] = (int(fr * 255), int(fg * 255), int(fb * 255), a)

    return img
