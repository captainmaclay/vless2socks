"""Тесты загрузчика xray.

Скачать настоящий релиз из песочницы нельзя (GitHub недоступен), поэтому
проверяется всё, что можно проверить без сети: выбор архива под платформу,
разбор .dgst во всех встречавшихся форматах, проверка хеша и безопасная
распаковка.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.get_xray import (  # noqa: E402
    DownloadError,
    asset_name,
    extract,
    parse_dgst,
    pick_asset,
)


class AssetNameTest(unittest.TestCase):
    def check(self, system: str, machine: str, expected: str):
        with mock.patch("platform.system", return_value=system), \
             mock.patch("platform.machine", return_value=machine):
            self.assertEqual(asset_name(), expected)

    def test_windows_x64(self):
        self.check("Windows", "AMD64", "Xray-windows-64.zip")

    def test_windows_arm(self):
        self.check("Windows", "ARM64", "Xray-windows-arm64-v8a.zip")

    def test_linux_x64(self):
        self.check("Linux", "x86_64", "Xray-linux-64.zip")

    def test_macos_arm(self):
        self.check("Darwin", "arm64", "Xray-macos-arm64-v8a.zip")

    def test_unknown_os(self):
        with mock.patch("platform.system", return_value="Plan9"):
            with self.assertRaises(DownloadError):
                asset_name()

    def test_unknown_arch(self):
        with mock.patch("platform.system", return_value="Linux"), \
             mock.patch("platform.machine", return_value="sparc"):
            with self.assertRaises(DownloadError):
                asset_name()


class PickAssetTest(unittest.TestCase):
    def release(self, names):
        return {
            "tag_name": "v25.3.6",
            "assets": [
                {"name": n, "browser_download_url": f"https://ex/{n}"} for n in names
            ],
        }

    def test_finds_zip_and_dgst(self):
        rel = self.release(["Xray-windows-64.zip", "Xray-windows-64.zip.dgst"])
        zip_url, dgst_url = pick_asset(rel, "Xray-windows-64.zip")
        self.assertEqual(zip_url, "https://ex/Xray-windows-64.zip")
        self.assertEqual(dgst_url, "https://ex/Xray-windows-64.zip.dgst")

    def test_missing_zip_lists_alternatives(self):
        rel = self.release(["Xray-linux-64.zip", "Xray-linux-64.zip.dgst"])
        with self.assertRaises(DownloadError) as ctx:
            pick_asset(rel, "Xray-windows-64.zip")
        self.assertIn("Xray-linux-64.zip", str(ctx.exception))

    def test_refuses_without_checksum(self):
        rel = self.release(["Xray-windows-64.zip"])
        with self.assertRaises(DownloadError) as ctx:
            pick_asset(rel, "Xray-windows-64.zip")
        self.assertIn("проверить хеш нечем", str(ctx.exception))


class ParseDgstTest(unittest.TestCase):
    DIGEST = "a" * 64

    def test_sha256_equals(self):
        text = f"MD5= 123\nSHA1= 456\nSHA256= {self.DIGEST}\n"
        self.assertEqual(parse_dgst(text), self.DIGEST)

    def test_sha2_256_variant(self):
        text = f"MD5= 123\nSHA2-256= {self.DIGEST}\n"
        self.assertEqual(parse_dgst(text), self.DIGEST)

    def test_colon_variant(self):
        text = f"sha256: {self.DIGEST}\n"
        self.assertEqual(parse_dgst(text), self.DIGEST)

    def test_uppercase_digest_is_normalised(self):
        text = f"SHA256= {self.DIGEST.upper()}\n"
        self.assertEqual(parse_dgst(text), self.DIGEST)

    def test_ignores_sha512(self):
        text = f"SHA512= {'b' * 128}\nSHA256= {self.DIGEST}\n"
        self.assertEqual(parse_dgst(text), self.DIGEST)

    def test_missing_sha256(self):
        with self.assertRaises(DownloadError):
            parse_dgst("MD5= 123\nSHA1= 456\n")

    def test_malformed_digest_is_rejected(self):
        with self.assertRaises(DownloadError):
            parse_dgst("SHA256= not-a-hash\n")


class ExtractTest(unittest.TestCase):
    def make_zip(self, path: Path, names: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in names.items():
                zf.writestr(name, data)

    def test_extracts_only_wanted_files(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            archive = tmp / "x.zip"
            self.make_zip(archive, {
                "xray": b"binary",
                "geoip.dat": b"geo",
                "geosite.dat": b"geo",
                "README.md": b"docs",
                "LICENSE": b"lic",
            })
            dest = tmp / "bin"
            written = extract(archive, dest)
            names = sorted(p.name for p in written)
            self.assertEqual(names, ["geoip.dat", "geosite.dat", "xray"])
            self.assertFalse((dest / "README.md").exists())

    def test_binary_becomes_executable(self):
        if sys.platform == "win32":
            self.skipTest("права на исполнение проверяются на POSIX")
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            archive = tmp / "x.zip"
            self.make_zip(archive, {"xray": b"binary"})
            written = extract(archive, tmp / "bin")
            self.assertTrue(written[0].stat().st_mode & 0o111)

    def test_rejects_archive_without_binary(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            archive = tmp / "x.zip"
            self.make_zip(archive, {"README.md": b"docs"})
            with self.assertRaises(DownloadError) as ctx:
                extract(archive, tmp / "bin")
            self.assertIn("формат релиза изменился", str(ctx.exception))

    def test_path_traversal_is_flattened(self):
        """Имя из архива берётся без каталогов — вылезти за dest нельзя."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            archive = tmp / "x.zip"
            self.make_zip(archive, {"../../xray": b"evil"})
            dest = tmp / "bin"
            written = extract(archive, dest)
            self.assertEqual(written[0].parent.resolve(), dest.resolve())
            self.assertFalse((tmp.parent / "xray").exists())


class ChecksumFlowTest(unittest.TestCase):
    """Проверка, что хеш действительно сверяется, а не просто печатается."""

    def test_mismatch_aborts_install(self):
        from tools import get_xray

        blob = b"not the real archive"
        good = hashlib.sha256(blob).hexdigest()
        bad = "f" * 64

        release = {
            "tag_name": "v1",
            "assets": [
                {"name": "Xray-linux-64.zip",
                 "browser_download_url": "https://ex/z"},
                {"name": "Xray-linux-64.zip.dgst",
                 "browser_download_url": "https://ex/d"},
            ],
        }

        def fake_fetch(url, timeout=60.0):
            if url.endswith("/d"):
                return f"SHA256= {bad}\n".encode()
            if url.endswith("/z"):
                return blob
            import json
            return json.dumps(release).encode()

        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(get_xray, "fetch", fake_fetch), \
             mock.patch("platform.system", return_value="Linux"), \
             mock.patch("platform.machine", return_value="x86_64"):
            with self.assertRaises(DownloadError) as ctx:
                get_xray.install(None, Path(d))
        message = str(ctx.exception)
        self.assertIn("SHA-256 не совпал", message)
        self.assertIn(good, message)


if __name__ == "__main__":
    unittest.main()
