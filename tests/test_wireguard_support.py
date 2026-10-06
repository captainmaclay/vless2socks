"""Tests for WireGuard protocol support in vless2socks."""

import unittest
from pathlib import Path

from vless2socks.config import AppConfig
from vless2socks.backend import resolve_backend, XRAY
from vless2socks.url import (
    WireGuardServer,
    SocksServer,
    VlessServer,
    parse_wireguard_url,
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


class WireGuardUrlTest(unittest.TestCase):
    def test_parse_wireguard_standard_params(self):
        url = (
            "wireguard://185.248.33.65:51820"
            "?pk=yAnql5C3WMyGSSROBsManwhLv2xysaKMYzlQ21iX098="
            "&peer_pk=xTIBA5rboUvK2htWhAeUxDrTCqJfQCY2uzZssoJ2UQA="
            "&local_address=10.0.0.2/32,fd00::2/128"
            "&psk=testPreSharedKey123="
            "&mtu=1420#🇫🇷 FRANCE WIREGUARD"
        )
        srv = parse_wireguard_url(url)
        self.assertIsInstance(srv, WireGuardServer)
        self.assertEqual(srv.address, "185.248.33.65")
        self.assertEqual(srv.port, 51820)
        self.assertEqual(srv.secret_key, "yAnql5C3WMyGSSROBsManwhLv2xysaKMYzlQ21iX098=")
        self.assertEqual(srv.peer_public_key, "xTIBA5rboUvK2htWhAeUxDrTCqJfQCY2uzZssoJ2UQA=")
        self.assertEqual(srv.local_address, ["10.0.0.2/32", "fd00::2/128"])
        self.assertEqual(srv.preshared_key, "testPreSharedKey123=")
        self.assertEqual(srv.mtu, 1420)
        self.assertEqual(srv.remark, "🇫🇷 FRANCE WIREGUARD")
        self.assertIn("185.248.33.65:51820", srv.describe())

    def test_parse_wg_short_scheme(self):
        url = "wg://vpn.example.com:51820?pk=secretPrivKey=&peer_pk=serverPubKey=#MyWireGuard"
        srv = parse_wireguard_url(url)
        self.assertEqual(srv.address, "vpn.example.com")
        self.assertEqual(srv.secret_key, "secretPrivKey=")
        self.assertEqual(srv.peer_public_key, "serverPubKey=")
        self.assertEqual(srv.remark, "MyWireGuard")

    def test_parse_wireguard_missing_keys_raises(self):
        with self.assertRaises(ConfigError):
            parse_wireguard_url("wireguard://1.2.3.4:51820?peer_pk=pubOnly")
        with self.assertRaises(ConfigError):
            parse_wireguard_url("wireguard://1.2.3.4:51820?pk=privOnly")

    def test_parse_proxy_url_dispatch(self):
        wg_url = "wireguard://1.2.3.4:51820?pk=privKey=&peer_pk=pubKey=#wg_test"
        srv = parse_proxy_url(wg_url)
        self.assertIsInstance(srv, WireGuardServer)

        vless_url = "vless://00000000-0000-0000-0000-000000000000@example.com:443?security=tls#vless_test"
        vless_srv = parse_proxy_url(vless_url)
        self.assertIsInstance(vless_srv, VlessServer)

        socks_url = "socks5://127.0.0.1:1080#socks_test"
        socks_srv = parse_proxy_url(socks_url)
        self.assertIsInstance(socks_srv, SocksServer)

    def test_server_from_mapping_wireguard(self):
        data = {
            "protocol": "wireguard",
            "address": "192.168.1.1",
            "port": 51820,
            "secretKey": "mySecretKey=",
            "publicKey": "myServerPubKey=",
            "localAddress": ["10.0.0.5/32"],
            "mtu": 1360,
            "remark": "MappingWG",
        }
        srv = server_from_mapping(data)
        self.assertIsInstance(srv, WireGuardServer)
        self.assertEqual(srv.address, "192.168.1.1")
        self.assertEqual(srv.secret_key, "mySecretKey=")
        self.assertEqual(srv.peer_public_key, "myServerPubKey=")
        self.assertEqual(srv.local_address, ["10.0.0.5/32"])
        self.assertEqual(srv.mtu, 1360)
        self.assertEqual(srv.remark, "MappingWG")


class WireGuardXrayConfigTest(unittest.TestCase):
    def test_build_xray_config_wireguard(self):
        srv = WireGuardServer(
            address="198.51.100.1",
            port=51820,
            secret_key="clientPrivateSecretKey=",
            peer_public_key="serverPublicKeyExpected=",
            local_address=["10.0.0.2/32"],
            preshared_key="presharedSecret=",
            mtu=1420,
        )
        cfg = AppConfig(
            server=srv,
            listen_host="127.0.0.1",
            listen_port=1085,
        )
        xray_cfg = build_xray_config(cfg)

        # Check inbounds
        inbounds = xray_cfg["inbounds"]
        self.assertEqual(len(inbounds), 2)
        self.assertEqual(inbounds[0]["tag"], "socks-in")
        self.assertEqual(inbounds[0]["port"], 1085)
        self.assertEqual(inbounds[1]["tag"], "http-in")
        self.assertEqual(inbounds[1]["port"], 11085)

        # Check outbound
        outbounds = xray_cfg["outbounds"]
        primary = outbounds[0]
        self.assertEqual(primary["tag"], "proxy")
        self.assertEqual(primary["protocol"], "wireguard")

        settings = primary["settings"]
        self.assertEqual(settings["secretKey"], "clientPrivateSecretKey=")
        self.assertEqual(settings["address"], ["10.0.0.2/32"])
        self.assertTrue(settings["noKernelTun"])
        self.assertEqual(settings["mtu"], 1420)

        peer = settings["peers"][0]
        self.assertEqual(peer["publicKey"], "serverPublicKeyExpected=")
        self.assertEqual(peer["endpoint"], "198.51.100.1:51820")
        self.assertEqual(peer["preSharedKey"], "presharedSecret=")

    def test_wireguard_backend_resolution(self):
        srv = WireGuardServer(
            address="1.2.3.4",
            port=51820,
            secret_key="kCJnfUpqH63zdgRKptChlYIgVpM5EgKLI7xvbT6lT3c=",
            peer_public_key="sB8IzSZfiCP5Qcvq7pbnS11o0KIWMiT0BQBUCB9PLQw=",
        )
        cfg = AppConfig(server=srv, backend="auto")
        choice = resolve_backend(cfg)
        self.assertEqual(choice.name, XRAY)

        # backend=python must raise ConfigError
        cfg_py = AppConfig(server=srv, backend="python")
        with self.assertRaises(ConfigError):
            resolve_backend(cfg_py)

    def test_redact_and_describe_config(self):
        srv = WireGuardServer(
            address="1.2.3.4",
            port=51820,
            secret_key="superSecretPrivateKey1234567890=",
            peer_public_key="serverPublicKey1234567890=",
            preshared_key="presharedSecretValue=",
        )
        cfg = AppConfig(server=srv, listen_port=1081)
        xray_cfg = build_xray_config(cfg)

        redacted = redact_config(xray_cfg)
        primary = redacted["outbounds"][0]["settings"]
        self.assertNotIn("superSecretPrivateKey1234567890=", primary["secretKey"])
        self.assertEqual(primary["peers"][0]["preSharedKey"], "***")

        desc = describe_config(xray_cfg)
        self.assertIn("outbound: wireguard 1.2.3.4:51820", desc)


class WireGuardGeoIpTest(unittest.TestCase):
    def test_geo_hint_from_wireguard_remark_and_host(self):
        url = "wireguard://185.248.33.65:51820?pk=a&peer_pk=b#🇫🇷 FRANCE 1 WIREGUARD"
        hint = geo_ip.extract_country_hint(url)
        self.assertEqual(hint["country"], "France")
        self.assertEqual(hint["flag"], "🇫🇷")

        url2 = "wireguard://fi.vpn.example.com:51820?pk=a&peer_pk=b"
        hint2 = geo_ip.extract_country_hint(url2)
        self.assertEqual(hint2["country"], "Finland")
        self.assertEqual(hint2["flag"], "🇫🇮")


if __name__ == "__main__":
    unittest.main()
