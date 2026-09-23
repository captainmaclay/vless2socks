"""Конфиг для xray проверяется побайтово.

Опечатку в имени поля иначе можно поймать только по невнятной ошибке от самого
xray, а его в песочнице нет — поэтому структура сверяется с документацией здесь.
"""

from __future__ import annotations

import json
import unittest
import uuid as uuid_mod

from vless2socks.config import AppConfig
from vless2socks.url import parse_vless_url
from vless2socks.xray.config_builder import (
    build_xray_config,
    describe_config,
    redact_config,
)

UUID = "00000000-0000-0000-0000-000000000000"

REALITY_URL = (
    f"vless://{UUID}@fi3.example.net:443?encryption=none&flow=xtls-rprx-vision"
    "&type=tcp&security=reality&sni=fi3.example.net&fp=edge"
    "&pbk=TestOnlyRealityPublicKeyAAAAAAAAAAAAAAAAAAA&sid=0123456789abcdef"
    "#%F0%9F%87%AB%F0%9F%87%AE%20FINLAND"
)


def config_for(url: str, **kw) -> AppConfig:
    cfg = AppConfig(
        server=parse_vless_url(url, strict=False),
        listen_host="127.0.0.1",
        listen_port=1080,
    )
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


class RealityConfigTest(unittest.TestCase):
    """Разбор настоящей ссылки, ради которой всё это и затевалось."""

    def setUp(self):
        self.config = build_xray_config(config_for(REALITY_URL))
        self.outbound = self.config["outbounds"][0]
        self.stream = self.outbound["streamSettings"]

    def test_outbound_points_at_the_server(self):
        self.assertEqual(self.outbound["protocol"], "vless")
        settings = self.outbound["settings"]
        self.assertEqual(settings["address"], "fi3.example.net")
        self.assertEqual(settings["port"], 443)

    def test_carries_uuid_encryption_and_flow(self):
        settings = self.outbound["settings"]
        self.assertEqual(settings["id"], UUID)
        self.assertEqual(settings["encryption"], "none")
        self.assertEqual(settings["flow"], "xtls-rprx-vision")

    def test_stream_is_tcp_reality(self):
        self.assertEqual(self.stream["network"], "tcp")
        self.assertEqual(self.stream["security"], "reality")
        self.assertNotIn("tlsSettings", self.stream)

    def test_reality_settings_match_link(self):
        reality = self.stream["realitySettings"]
        self.assertEqual(reality["serverName"], "fi3.example.net")
        self.assertEqual(
            reality["publicKey"], "TestOnlyRealityPublicKeyAAAAAAAAAAAAAAAAAAA"
        )
        self.assertEqual(reality["shortId"], "0123456789abcdef")
        self.assertEqual(reality["fingerprint"], "edge")

    def test_no_spiderx_when_link_has_none(self):
        self.assertNotIn("spiderX", self.stream["realitySettings"])

    def test_config_is_json_serialisable(self):
        json.dumps(self.config)


class OutboundShapeTest(unittest.TestCase):
    """Форма outbound менялась: PR #5101 убрал вложенность vnext/users.

    Официальный пример XTLS для VLESS+Vision+REALITY использует плоскую форму,
    поэтому она у нас основная, а vnext — запасная для старых бинарников.
    """

    def test_modern_shape_is_flat_like_official_example(self):
        settings = build_xray_config(config_for(REALITY_URL))["outbounds"][0]["settings"]
        self.assertNotIn("vnext", settings)
        self.assertNotIn("users", settings)
        self.assertEqual(settings["address"], "fi3.example.net")
        self.assertEqual(settings["port"], 443)
        self.assertEqual(settings["id"], UUID)
        self.assertEqual(settings["encryption"], "none")
        self.assertEqual(settings["flow"], "xtls-rprx-vision")

    def test_legacy_shape_keeps_vnext_nesting(self):
        settings = build_xray_config(
            config_for(REALITY_URL), legacy_vnext=True
        )["outbounds"][0]["settings"]
        self.assertIn("vnext", settings)
        vnext = settings["vnext"][0]
        self.assertEqual(vnext["address"], "fi3.example.net")
        user = vnext["users"][0]
        self.assertEqual(user["id"], UUID)
        self.assertEqual(user["flow"], "xtls-rprx-vision")

    def test_both_shapes_carry_identical_stream_settings(self):
        modern = build_xray_config(config_for(REALITY_URL))["outbounds"][0]
        legacy = build_xray_config(
            config_for(REALITY_URL), legacy_vnext=True
        )["outbounds"][0]
        self.assertEqual(modern["streamSettings"], legacy["streamSettings"])

    def test_flow_omitted_in_both_shapes_when_absent(self):
        url = f"vless://{UUID}@h.example:443?security=tls&type=tcp"
        flat = build_xray_config(config_for(url))["outbounds"][0]["settings"]
        nested = build_xray_config(
            config_for(url), legacy_vnext=True
        )["outbounds"][0]["settings"]
        self.assertNotIn("flow", flat)
        self.assertNotIn("flow", nested["vnext"][0]["users"][0])

    def test_describe_names_the_shape(self):
        self.assertIn(
            "плоская форма", describe_config(build_xray_config(config_for(REALITY_URL)))
        )
        self.assertIn(
            "vnext",
            describe_config(build_xray_config(config_for(REALITY_URL), legacy_vnext=True)),
        )

    def test_redaction_works_for_both_shapes(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                safe = redact_config(
                    build_xray_config(config_for(REALITY_URL), legacy_vnext=legacy)
                )
                self.assertNotIn(UUID, json.dumps(safe))


class InboundTest(unittest.TestCase):
    def test_noauth_by_default(self):
        inbound = build_xray_config(config_for(REALITY_URL))["inbounds"][0]
        self.assertEqual(inbound["protocol"], "socks")
        self.assertEqual(inbound["listen"], "127.0.0.1")
        self.assertEqual(inbound["port"], 1080)
        self.assertEqual(inbound["settings"]["auth"], "noauth")
        self.assertTrue(inbound["settings"]["udp"])
        self.assertNotIn("accounts", inbound["settings"])

    def test_password_auth_when_credentials_set(self):
        cfg = config_for(REALITY_URL, username="bob", password="hunter2")
        inbound = build_xray_config(cfg)["inbounds"][0]
        self.assertEqual(inbound["settings"]["auth"], "password")
        self.assertEqual(
            inbound["settings"]["accounts"], [{"user": "bob", "pass": "hunter2"}]
        )

    def test_udp_can_be_disabled(self):
        cfg = config_for(REALITY_URL, udp_enabled=False)
        inbound = build_xray_config(cfg)["inbounds"][0]
        self.assertFalse(inbound["settings"]["udp"])

    def test_sniffing_enabled_for_remote_dns(self):
        inbound = build_xray_config(config_for(REALITY_URL))["inbounds"][0]
        self.assertTrue(inbound["sniffing"]["enabled"])
        self.assertIn("tls", inbound["sniffing"]["destOverride"])


class TransportTest(unittest.TestCase):
    def test_plain_tls_tcp(self):
        stream = build_xray_config(
            config_for(
                f"vless://{UUID}@h.example:443?security=tls&type=tcp"
                "&sni=cdn.example&alpn=h2%2Chttp%2F1.1&fp=chrome"
            )
        )["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["network"], "tcp")
        self.assertEqual(stream["security"], "tls")
        self.assertEqual(stream["tlsSettings"]["serverName"], "cdn.example")
        self.assertEqual(stream["tlsSettings"]["alpn"], ["h2", "http/1.1"])
        self.assertEqual(stream["tlsSettings"]["fingerprint"], "chrome")
        self.assertFalse(stream["tlsSettings"]["allowInsecure"])

    def test_allow_insecure_reaches_config(self):
        stream = build_xray_config(
            config_for(f"vless://{UUID}@h.example:443?security=tls&allowInsecure=1")
        )["outbounds"][0]["streamSettings"]
        self.assertTrue(stream["tlsSettings"]["allowInsecure"])

    def test_websocket(self):
        stream = build_xray_config(
            config_for(
                f"vless://{UUID}@h.example:443?security=tls&type=ws"
                "&path=%2Fray&host=cdn.example"
            )
        )["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["network"], "ws")
        self.assertEqual(stream["wsSettings"]["path"], "/ray")
        self.assertEqual(stream["wsSettings"]["headers"]["Host"], "cdn.example")

    def test_grpc(self):
        stream = build_xray_config(
            config_for(
                f"vless://{UUID}@h.example:443?security=tls&type=grpc"
                "&serviceName=TunSvc"
            )
        )["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["network"], "grpc")
        self.assertEqual(stream["grpcSettings"]["serviceName"], "TunSvc")

    def test_httpupgrade(self):
        stream = build_xray_config(
            config_for(
                f"vless://{UUID}@h.example:443?security=tls&type=httpupgrade"
                "&path=%2Fup&host=cdn.example"
            )
        )["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["network"], "httpupgrade")
        self.assertEqual(stream["httpupgradeSettings"]["path"], "/up")
        self.assertEqual(stream["httpupgradeSettings"]["host"], "cdn.example")

    def test_plain_tcp_no_security(self):
        stream = build_xray_config(
            config_for(f"vless://{UUID}@h.example:8080?type=tcp")
        )["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["security"], "none")
        self.assertNotIn("tlsSettings", stream)
        self.assertNotIn("realitySettings", stream)

    def test_flow_omitted_when_empty(self):
        settings = build_xray_config(
            config_for(f"vless://{UUID}@h.example:443?security=tls")
        )["outbounds"][0]["settings"]
        self.assertNotIn("flow", settings)


class RedactionTest(unittest.TestCase):
    def test_uuid_and_key_are_masked(self):
        cfg = config_for(REALITY_URL, username="bob", password="hunter2")
        safe = redact_config(build_xray_config(cfg))
        dumped = json.dumps(safe)
        self.assertNotIn(UUID, dumped)
        self.assertNotIn("hunter2", dumped)
        self.assertNotIn("TestOnlyRealityPublicKeyAAAAAAAAAAAAAAAAAAA", dumped)
        self.assertIn("0000...0000", dumped)

    def test_original_config_is_not_mutated(self):
        cfg = config_for(REALITY_URL)
        original = build_xray_config(cfg)
        redact_config(original)
        self.assertEqual(
            original["outbounds"][0]["settings"]["id"], UUID
        )


class DescribeTest(unittest.TestCase):
    def test_summary_mentions_key_facts(self):
        text = describe_config(build_xray_config(config_for(REALITY_URL)))
        self.assertIn("socks 127.0.0.1:1080", text)
        self.assertIn("vless fi3.example.net:443", text)
        self.assertIn("security=reality", text)
        self.assertIn("xtls-rprx-vision", text)
        self.assertIn("fingerprint=edge", text)


class SendThroughTest(unittest.TestCase):
    def test_send_through_explicit_ip(self):
        cfg = config_for(REALITY_URL, send_through="192.168.1.50")
        built = build_xray_config(cfg)
        self.assertEqual(built["outbounds"][0].get("sendThrough"), "192.168.1.50")

    def test_send_through_auto(self):
        cfg = config_for(REALITY_URL, send_through="auto")
        built = build_xray_config(cfg)
        # Should either be a valid IP or omitted if not detectable
        val = built["outbounds"][0].get("sendThrough")
        if val is not None:
            self.assertRegex(val, r"^\d+\.\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
