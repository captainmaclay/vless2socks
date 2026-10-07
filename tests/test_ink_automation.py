"""Unit and integration tests for vless2socks.ink automation subsystem."""

import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from vless2socks.ink.colors import THEMES, recolor_chrome_image, recolor_monochrome_hue
from vless2socks.ink.icon_engine import find_binary, save_multires_ico
from vless2socks.ink.launchers import build_chrome_bat, build_xshell_bat, configure_xshell_proxy
from vless2socks.ink.pipeline import generate_proxy_workspace, load_instances_ports
from vless2socks.ink.shortcuts import create_or_update_shortcut, ensure_folder_aliases


class InkAutomationTestCase(unittest.TestCase):
    """Test suite covering icons, launchers, shortcuts, and orchestration pipeline."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ink_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_themes_completeness(self):
        """Verifies that all standard proxy color themes are present and configured."""
        expected_themes = ["gold", "blue", "purple", "orange", "black", "green", "red", "cyan", "pink"]
        for t in expected_themes:
            self.assertIn(t, THEMES)
            self.assertIn("core_hsv", THEMES[t])
            self.assertIn("bright_hsv", THEMES[t])

    def test_recolor_chrome_image(self):
        """Verifies that Chrome icon recoloring works and preserves white separator ring."""
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        pixels = img.load()
        pixels[10, 10] = (255, 255, 255, 255) # White pixel (separator)
        pixels[20, 20] = (220, 30, 30, 255)   # Saturated red pixel

        recolored = recolor_chrome_image(img, "cyan")
        r_pix = recolored.load()

        self.assertEqual(r_pix[10, 10], (255, 255, 255, 255))
        cr, cg, cb, ca = r_pix[20, 20]
        self.assertEqual(ca, 255)
        self.assertGreater(cb, cr)

    def test_recolor_monochrome_hue(self):
        """Tests general application icon recoloring to target hue."""
        img = Image.new("RGBA", (16, 16), (255, 100, 0, 255))
        recolored = recolor_monochrome_hue(img, target_hue=0.51)
        r, g, b, a = recolored.getpixel((8, 8))
        self.assertEqual(a, 255)
        self.assertGreater(b, r)

    def test_save_multires_ico(self):
        """Verifies multi-resolution ICO file compilation."""
        f256 = Image.new("RGBA", (256, 256), (255, 0, 0, 255))
        f48 = Image.new("RGBA", (48, 48), (255, 0, 0, 255))
        f16 = Image.new("RGBA", (16, 16), (255, 0, 0, 255))

        out_ico = os.path.join(self.temp_dir, "test.ico")
        save_multires_ico([f256, f48, f16], out_ico)
        self.assertTrue(os.path.isfile(out_ico))

        # Check binary header count of icons
        with open(out_ico, "rb") as fp:
            res, typ, count = struct.unpack("<HHH", fp.read(6))
            self.assertEqual(count, 3)

        with Image.open(out_ico) as im_read:
            self.assertIn((256, 256), im_read.info.get("sizes", set()))

    def test_build_chrome_bat(self):
        """Tests Chrome batch launcher creation."""
        bat_file = os.path.join(self.temp_dir, "Chrome_Socket_1081.bat")
        build_chrome_bat(1081, out_bat_path=bat_file)
        self.assertTrue(os.path.isfile(bat_file))

        with open(bat_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("socks5://127.0.0.1:1081", content)
        self.assertIn('--user-data-dir="%LOCALAPPDATA%\\Google\\Chrome\\Proxy1081"', content)
        self.assertIn("chcp 65001", content)

    def test_build_xshell_bat(self):
        """Tests Xshell batch launcher creation."""
        bat_file = os.path.join(self.temp_dir, "Xshell_Socket_1030.bat")
        build_xshell_bat(1030, out_bat_path=bat_file)
        self.assertTrue(os.path.isfile(bat_file))

        with open(bat_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("set ALL_PROXY=socks5://127.0.0.1:1030", content)
        self.assertIn("set HTTP_PROXY=http://127.0.0.1:11030", content)
        self.assertIn("Xshell.exe", content)

    def test_load_instances_ports(self):
        """Tests loading ports from instances.json format."""
        mock_json = os.path.join(self.temp_dir, "instances.json")
        with open(mock_json, "w", encoding="utf-8") as f:
            json.dump([
                {"listen": "127.0.0.1:1015"},
                {"listen": "127.0.0.1:1030"},
                {"listen": "127.0.0.1:1085"}
            ], f)

        ports = load_instances_ports(mock_json)
        self.assertEqual(ports, [1015, 1030, 1085])

    def test_ensure_folder_aliases(self):
        """Tests junction/folder alias creation for ink, link, and lnk."""
        ensure_folder_aliases(self.temp_dir, "ink")
        self.assertTrue(os.path.isdir(os.path.join(self.temp_dir, "ink")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "link")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "lnk")))

    def test_create_shortcut(self):
        """Tests PowerShell .lnk shortcut generation."""
        target = os.path.join(self.temp_dir, "test.bat")
        with open(target, "w") as f:
            f.write("@echo off\n")
        lnk = os.path.join(self.temp_dir, "ink", "Test.lnk")

        ok = create_or_update_shortcut(lnk, target)
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(lnk))

    def test_generate_proxy_workspace_pipeline(self):
        """Tests end-to-end workspace generator pipeline."""
        mock_json = os.path.join(self.temp_dir, "instances.json")
        with open(mock_json, "w", encoding="utf-8") as f:
            json.dump([
                {"listen": "127.0.0.1:1015"},
                {"listen": "127.0.0.1:1030"}
            ], f)

        res = generate_proxy_workspace(
            target_dir=os.path.join(self.temp_dir, "Proxy"),
            instances_path=mock_json,
            include_xshell=True,
            xshell_port=1030,
            update_desktop=False
        )

        ws = Path(res["workspace"])
        self.assertTrue((ws / "ico").is_dir())
        self.assertTrue((ws / "ink").is_dir())
        self.assertTrue((ws / "Chrome_Socket_1015.bat").is_file())
        self.assertTrue((ws / "Chrome_Socket_1030.bat").is_file())
        self.assertTrue((ws / "Xshell_Socket_1030.bat").is_file())
        self.assertTrue((ws / "ink" / "Chrome Socket 1015.lnk").is_file())
        self.assertTrue((ws / "ink" / "Xshell Socket 1030.lnk").is_file())


if __name__ == "__main__":
    unittest.main()
