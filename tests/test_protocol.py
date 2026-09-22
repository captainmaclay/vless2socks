import unittest
import uuid as uuid_mod

from vless2socks.protocol import (
    ATYP_DOMAIN,
    ATYP_IPV4,
    ATYP_IPV6,
    CMD_TCP,
    CMD_UDP,
    ProtocolError,
    build_request_header,
    build_response_header,
    decode_address,
    encode_address,
    parse_request_header,
)

UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"
UUID_BYTES = uuid_mod.UUID(UUID).bytes


class AddressTest(unittest.TestCase):
    def test_ipv4(self):
        raw = encode_address("192.0.2.10")
        self.assertEqual(raw[0], ATYP_IPV4)
        self.assertEqual(decode_address(raw), ("192.0.2.10", 5))

    def test_ipv6(self):
        raw = encode_address("2001:db8::1")
        self.assertEqual(raw[0], ATYP_IPV6)
        self.assertEqual(decode_address(raw), ("2001:db8::1", 17))

    def test_domain(self):
        raw = encode_address("example.com")
        self.assertEqual(raw[0], ATYP_DOMAIN)
        self.assertEqual(raw[1], len("example.com"))
        self.assertEqual(decode_address(raw), ("example.com", 13))

    def test_idn_domain_becomes_punycode(self):
        raw = encode_address("пример.рф")
        host, _ = decode_address(raw)
        self.assertTrue(host.startswith("xn--"))

    def test_rejects_empty(self):
        with self.assertRaises(ProtocolError):
            encode_address("")

    def test_rejects_truncated(self):
        with self.assertRaises(ProtocolError):
            decode_address(bytes([ATYP_IPV4, 1, 2]))

    def test_rejects_unknown_atyp(self):
        with self.assertRaises(ProtocolError):
            decode_address(bytes([0x09]))


class RequestHeaderTest(unittest.TestCase):
    def test_roundtrip_tcp_domain(self):
        header = build_request_header(UUID_BYTES, CMD_TCP, "example.com", 443)
        req = parse_request_header(header + b"payload")
        self.assertEqual(req.version, 0)
        self.assertEqual(req.user_id, UUID)
        self.assertEqual(req.command, CMD_TCP)
        self.assertEqual(req.host, "example.com")
        self.assertEqual(req.port, 443)
        self.assertEqual(req.addons, b"")
        self.assertEqual(req.header_len, len(header))

    def test_roundtrip_udp_ipv4(self):
        header = build_request_header(UUID_BYTES, CMD_UDP, "8.8.8.8", 53)
        req = parse_request_header(header)
        self.assertEqual(req.command, CMD_UDP)
        self.assertEqual((req.host, req.port), ("8.8.8.8", 53))

    def test_layout_matches_spec(self):
        header = build_request_header(UUID_BYTES, CMD_TCP, "1.1.1.1", 80)
        self.assertEqual(header[0], 0x00)               # версия
        self.assertEqual(header[1:17], UUID_BYTES)      # UUID
        self.assertEqual(header[17], 0)                 # длина addons
        self.assertEqual(header[18], CMD_TCP)           # команда
        self.assertEqual(header[19:21], b"\x00\x50")    # порт 80 big-endian
        self.assertEqual(header[21], ATYP_IPV4)
        self.assertEqual(header[22:26], bytes([1, 1, 1, 1]))
        self.assertEqual(len(header), 26)

    def test_addons_roundtrip(self):
        header = build_request_header(
            UUID_BYTES, CMD_TCP, "h.test", 8080, addons=b"\x01\x02\x03"
        )
        req = parse_request_header(header)
        self.assertEqual(req.addons, b"\x01\x02\x03")
        self.assertEqual(req.host, "h.test")

    def test_partial_header_raises(self):
        header = build_request_header(UUID_BYTES, CMD_TCP, "example.com", 443)
        for cut in range(1, len(header)):
            with self.assertRaises(ProtocolError):
                parse_request_header(header[:cut])

    def test_rejects_bad_uuid_length(self):
        with self.assertRaises(ProtocolError):
            build_request_header(b"\x00" * 15, CMD_TCP, "h", 80)

    def test_rejects_bad_port(self):
        with self.assertRaises(ProtocolError):
            build_request_header(UUID_BYTES, CMD_TCP, "h", 0)
        with self.assertRaises(ProtocolError):
            build_request_header(UUID_BYTES, CMD_TCP, "h", 70000)

    def test_rejects_unknown_command(self):
        with self.assertRaises(ProtocolError):
            build_request_header(UUID_BYTES, 0x09, "h", 80)


class ResponseHeaderTest(unittest.TestCase):
    def test_empty_addons(self):
        self.assertEqual(build_response_header(), b"\x00\x00")

    def test_with_addons(self):
        self.assertEqual(build_response_header(b"ab"), b"\x00\x02ab")


if __name__ == "__main__":
    unittest.main()
