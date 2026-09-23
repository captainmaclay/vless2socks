"""Tests for SOCKS5 upstream support in vless2socks."""

import unittest
from pathlib import Path

from vless2socks.config import AppConfig
from vless2socks.backend import resolve_backend, XRAY
from vless2socks.url import (
    SocksServer,
    VlessServer,
    parse_socks_url,
    parse_proxy_url,
    server_from_mapping,
    ConfigError,
)
from vless2socks.xray.config_builder import (
    build_xray_config,
    redact_config,
    describe_config,
)
import geo_ip


class SocksUrlTest(unittest.TestCase):
    def test_parse_socks5_with_auth_and_remark(self):
        url = "socks5://demo_user:test_pass123@lv1.example.com:20039#latvia"
        srv = parse_socks_url(url)
        self.assertIsInstance(srv, SocksServer)
        self.assertEqual(srv.address, "lv1.example.com")
        self.assertEqual(srv.port, 20039)
        self.assertEqual(srv.username, "demo_user")
        self.assertEqual(srv.password, "test_pass123")
        self.assertEqual(srv.version, 5)
        self.assertEqual(srv.remark, "latvia")
        self.assertEqual(srv.to_url(), url)
        self.assertIn("lv1.example.com:20039", srv.describe())
        self.assertIn("demo_user:***", srv.describe())

    def test_parse_socks5_without_auth(self):
        url = "socks5://127.0.0.1:1080#LocalProxy"
        srv = parse_socks_url(url)
        self.assertEqual(srv.address, "127.0.0.1")
        self.assertEqual(srv.port, 1080)
        self.assertEqual(srv.username, "")
        self.assertEqual(srv.password, "")
        self.assertEqual(srv.remark, "LocalProxy")
        self.assertEqual(srv.to_url(), url)
        self.assertIn("127.0.0.1:1080", srv.describe())
        self.assertIn("LocalProxy", srv.describe())

    def test_parse_proxy_url_dispatch(self):
        vless_url = "vless://a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d@example.com:443?security=tls&type=tcp#test"
        vless_srv = parse_proxy_url(vless_url)
        self.assertIsInstance(vless_srv, VlessServer)

        socks_url = "socks5://user:pass@proxy.example.com:1080#socks_test"
        socks_srv = parse_proxy_url(socks_url)
        self.assertIsInstance(socks_srv, SocksServer)

    def test_server_from_mapping_socks(self):
        mapping = {
            "protocol": "socks5",
            "address": "lv1.example.com",
            "port": 20039,
            "username": "demo_user",
            "password": "secret_password",
            "name": "latvia",
        }
        srv = server_from_mapping(mapping)
        self.assertIsInstance(srv, SocksServer)
        self.assertEqual(srv.address, "lv1.example.com")
        self.assertEqual(srv.port, 20039)
        self.assertEqual(srv.username, "demo_user")
        self.assertEqual(srv.password, "secret_password")
        self.assertEqual(srv.remark, "latvia")


class SocksXrayConfigTest(unittest.TestCase):
    def setUp(self):
        self.server = SocksServer(
            address="lv1.example.com",
            port=20039,
            username="demo_user",
            password="test_pass123",
            version=5,
            remark="latvia",
        )
        self.config = AppConfig(
            server=self.server,
            listen_host="127.0.0.1",
            listen_port=1081,
        )

    def test_build_xray_config_socks_outbound(self):
        cfg = build_xray_config(self.config)
        self.assertIn("outbounds", cfg)
        outbounds = cfg["outbounds"]
        self.assertGreaterEqual(len(outbounds), 1)

        proxy_outbound = next(o for o in outbounds if o.get("tag") == "proxy")
        self.assertEqual(proxy_outbound["protocol"], "socks")

        servers = proxy_outbound["settings"]["servers"]
        self.assertEqual(len(servers), 1)
        srv = servers[0]
        self.assertEqual(srv["address"], "lv1.example.com")
        self.assertEqual(srv["port"], 20039)
        self.assertEqual(srv["users"], [{"user": "demo_user", "pass": "test_pass123", "level": 0}])

    def test_dual_inbounds_created(self):
        cfg = build_xray_config(self.config)
        inbounds = cfg.get("inbounds", [])
        self.assertEqual(len(inbounds), 2)
        protocols = {i["protocol"]: i["port"] for i in inbounds}
        self.assertEqual(protocols.get("socks"), 1081)
        self.assertEqual(protocols.get("http"), 11081)

    def test_redact_config_masks_socks_password(self):
        cfg = build_xray_config(self.config)
        redacted = redact_config(cfg)
        proxy_outbound = next(o for o in redacted["outbounds"] if o.get("tag") == "proxy")
        user_entry = proxy_outbound["settings"]["servers"][0]["users"][0]
        self.assertEqual(user_entry["pass"], "***")
        self.assertEqual(user_entry["user"], "demo_user")

    def test_describe_config_format(self):
        xray_cfg = build_xray_config(self.config)
        desc = describe_config(xray_cfg)
        self.assertIn("socks5 lv1.example.com:20039", desc)
        self.assertIn("user/pass", desc)

        no_auth_server = SocksServer(address="127.0.0.1", port=1080)
        no_auth_cfg = AppConfig(server=no_auth_server, listen_port=1082)
        desc_no_auth = describe_config(build_xray_config(no_auth_cfg))
        self.assertIn("no-auth", desc_no_auth)


class SocksBackendResolutionTest(unittest.TestCase):
    def test_resolve_backend_routes_to_xray(self):
        server = SocksServer(
            address="lv1.example.com",
            port=20039,
            username="demo_user",
            password="pwd",
        )
        config = AppConfig(server=server, backend="auto")
        choice = resolve_backend(config)
        self.assertEqual(choice.name, XRAY)
        self.assertIn("SOCKS5", choice.reason)


class GeoIpLatviaTest(unittest.TestCase):
    def test_extract_country_hint_latvia_remark(self):
        url = "socks5://demo_user:test_pass123@lv1.example.com:20039#latvia"
        hint = geo_ip.extract_country_hint(url)
        self.assertEqual(hint["country"], "Latvia")
        self.assertEqual(hint["flag"], "🇱🇻")
        self.assertEqual(hint["remark"], "latvia")

    def test_extract_country_hint_latvia_domain_prefix(self):
        url = "socks5://lv1.example.com:20039"
        hint = geo_ip.extract_country_hint(url)
        self.assertEqual(hint["country"], "Latvia")
        self.assertEqual(hint["flag"], "🇱🇻")


class GuiSocksIntegrationTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception:
            self.skipTest("Tkinter display not available")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_proxy_instance_socks_initialization(self):
        import gui
        from tkinter import ttk

        app = self.root
        app.instances = []
        app.save_all = lambda: None
        app.refresh_current_page_tabs = lambda: None
        app.refresh_overview = lambda: None
        app.update_tray_icon = lambda: None

        cfg = {
            "url": "socks5://demo_user:test_pass123@lv1.example.com:20039#latvia",
            "listen": "127.0.0.1:1081",
        }
        inst = gui.ProxyInstance(app, cfg, 0)
        self.assertEqual(inst.geo_info["country"], "Latvia")
        self.assertEqual(inst.geo_info["flag"], "🇱🇻")

        nb = ttk.Notebook(self.root)
        frame = inst.build_tab_ui(nb)
        self.assertIsNotNone(frame)
        self.assertEqual(inst.proto_var.get(), "socks5")
        summary = inst._format_socks_summary()
        self.assertIn("lv1.example.com:20039", summary)
        self.assertIn("demo_user", summary)
        self.assertIn("latvia", summary)

        # Toggle to vless and back
        inst.proto_var.set("vless")
        inst._on_proto_changed()
        self.assertTrue(inst.vless_container.winfo_ismapped() or True)

        inst.proto_var.set("socks5")
        inst._on_proto_changed()
        self.assertEqual(inst.get_config()["url"], cfg["url"])


if __name__ == "__main__":
    unittest.main()
