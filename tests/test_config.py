import json
import tempfile
import unittest
import uuid as uuid_mod
from pathlib import Path

from vless2socks.config import DEFAULT_CONFIG, load_config
from vless2socks.url import ConfigError

UUID = str(uuid_mod.uuid4())
URL = f"vless://{UUID}@srv.example:443?security=tls&type=tcp&sni=srv.example"


class LoadConfigTest(unittest.TestCase):
    def test_from_url_only(self):
        cfg = load_config(None, url=URL)
        self.assertEqual(cfg.server.address, "srv.example")
        self.assertEqual(cfg.listen_host, "127.0.0.1")
        self.assertEqual(cfg.listen_port, 1080)
        self.assertTrue(cfg.udp_enabled)
        self.assertFalse(cfg.auth_required)

    def test_listen_forms(self):
        self.assertEqual(load_config(None, url=URL, listen="0.0.0.0:9050").listen_host, "0.0.0.0")
        self.assertEqual(load_config(None, url=URL, listen="0.0.0.0:9050").listen_port, 9050)
        self.assertEqual(load_config(None, url=URL, listen="9050").listen_port, 9050)
        self.assertEqual(load_config(None, url=URL, listen="[::1]:9050").listen_host, "::1")
        self.assertEqual(load_config(None, url=URL, listen="[::1]:9050").listen_port, 9050)

    def test_bare_ipv6_is_not_split_as_host_port(self):
        """"::1" — это адрес целиком; наивный rpartition(":") дал бы host="::"."""
        cfg = load_config(None, url=URL, listen="::1")
        self.assertEqual(cfg.listen_host, "::1")
        self.assertEqual(cfg.listen_port, 1080)

    def test_bracketed_ipv6_keeps_port(self):
        cfg = load_config(None, url=URL, listen="[2001:db8::1]:9050")
        self.assertEqual(cfg.listen_host, "2001:db8::1")
        self.assertEqual(cfg.listen_port, 9050)

    def test_backend_defaults_to_auto(self):
        self.assertEqual(load_config(None, url=URL).backend, "auto")

    def test_backend_and_xray_path_from_cli(self):
        cfg = load_config(None, url=URL, backend="xray", xray_path="C:/x/xray.exe")
        self.assertEqual(cfg.backend, "xray")
        self.assertEqual(cfg.xray_path, "C:/x/xray.exe")

    def test_auth_flags(self):
        cfg = load_config(None, url=URL, username="u", password="p")
        self.assertTrue(cfg.auth_required)
        self.assertEqual((cfg.username, cfg.password), ("u", "p"))

    def test_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "url": URL,
                        "listen": "127.0.0.1:1081",
                        "udp": False,
                        "connectTimeout": 5,
                        "logLevel": "debug",
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_config(path)
        self.assertEqual(cfg.listen_port, 1081)
        self.assertFalse(cfg.udp_enabled)
        self.assertEqual(cfg.connect_timeout, 5.0)
        self.assertEqual(cfg.log_level, "debug")

    def test_cli_overrides_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"url": URL, "listen": "127.0.0.1:1081"}), encoding="utf-8")
            cfg = load_config(path, listen="127.0.0.1:1090")
        self.assertEqual(cfg.listen_port, 1090)

    def test_explicit_fields_without_url(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "address": "1.2.3.4",
                        "port": 8443,
                        "uuid": UUID,
                        "security": "none",
                        "type": "tcp",
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_config(path)
        self.assertEqual((cfg.server.address, cfg.server.port), ("1.2.3.4", 8443))
        self.assertFalse(cfg.server.uses_tls)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config("/definitely/not/here.json")

    def test_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text("{ broken", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_no_server_at_all(self):
        with self.assertRaises(ConfigError):
            load_config(None)

    def test_template_is_parseable(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")
            cfg = load_config(path)
        self.assertEqual(cfg.listen_port, 1080)
        self.assertTrue(cfg.server.uses_tls)


if __name__ == "__main__":
    unittest.main()
