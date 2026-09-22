import unittest

from vless2socks.url import ConfigError, parse_vless_url

UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"


class ParseVlessUrlTest(unittest.TestCase):
    def test_full_tls_url(self):
        s = parse_vless_url(
            f"vless://{UUID}@example.com:443"
            "?encryption=none&security=tls&sni=cdn.example.com&type=tcp"
            "&fp=chrome&alpn=h2%2Chttp%2F1.1#My%20Server"
        )
        self.assertEqual(s.address, "example.com")
        self.assertEqual(s.port, 443)
        self.assertEqual(s.user_id, UUID)
        self.assertEqual(s.security, "tls")
        self.assertEqual(s.sni, "cdn.example.com")
        self.assertEqual(s.alpn, ("h2", "http/1.1"))
        self.assertEqual(s.fingerprint, "chrome")
        self.assertEqual(s.remark, "My Server")
        self.assertTrue(s.uses_tls)
        self.assertEqual(len(s.uuid_bytes), 16)

    def test_sni_defaults_to_address(self):
        s = parse_vless_url(f"vless://{UUID}@example.com:443?security=tls")
        self.assertEqual(s.sni, "example.com")

    def test_plain_tcp(self):
        s = parse_vless_url(f"vless://{UUID}@1.2.3.4:8080?type=tcp")
        self.assertEqual(s.security, "none")
        self.assertFalse(s.uses_tls)
        self.assertEqual(s.sni, "")

    def test_ipv6_host(self):
        s = parse_vless_url(f"vless://{UUID}@[2001:db8::1]:443?security=tls")
        self.assertEqual(s.address, "2001:db8::1")

    def test_allow_insecure(self):
        s = parse_vless_url(f"vless://{UUID}@h:443?security=tls&allowInsecure=1")
        self.assertTrue(s.allow_insecure)

    def test_rejects_wrong_scheme(self):
        with self.assertRaises(ConfigError):
            parse_vless_url(f"vmess://{UUID}@example.com:443")

    def test_rejects_bad_uuid(self):
        with self.assertRaises(ConfigError):
            parse_vless_url("vless://not-a-uuid@example.com:443")

    def test_rejects_missing_port(self):
        with self.assertRaises(ConfigError):
            parse_vless_url(f"vless://{UUID}@example.com")

    def test_rejects_unsupported_transport(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_vless_url(f"vless://{UUID}@example.com:443?type=ws&path=/x")
        self.assertIn("не поддерживается", str(ctx.exception))

    def test_rejects_reality(self):
        with self.assertRaises(ConfigError):
            parse_vless_url(f"vless://{UUID}@example.com:443?security=reality&pbk=x")

    def test_rejects_xtls_flow(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_vless_url(
                f"vless://{UUID}@example.com:443?security=tls&flow=xtls-rprx-vision"
            )
        self.assertIn("XTLS", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
