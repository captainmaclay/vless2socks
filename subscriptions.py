"""Proxy subscriptions: fetch a subscription URL and turn its vless:// links into proxies.

A subscription is a URL whose body is a list of share links, one per line,
usually base64-encoded as a whole (the v2rayN / Xray convention). Only vless://
links are imported for now; anything else is counted and skipped.

New proxies get listen ports from the configured start port upwards (default
1080), skipping ports used by other proxies or busy on the system. Each proxy
also needs its HTTP port (SOCKS port + 10000) free.
"""

from __future__ import annotations

import base64
import binascii
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

import settings_manager
from vless2socks.url import ConfigError, parse_vless_url

START_PORT_KEY = "proxy_start_port"
DEFAULT_START_PORT = 1080
MIN_START_PORT = 1024
#: The HTTP inbound sits at SOCKS port + 10000, so the SOCKS port must leave room for it
MAX_START_PORT = 65535 - 10000
HTTP_PORT_OFFSET = 10000
SUBSCRIPTIONS_KEY = "subscriptions"
SUB_CFG_KEY = "subscription"
TYPE_VLESS = "vless"
MAX_BODY_BYTES = 5 * 1024 * 1024
USER_AGENT = "v2rayN/6.0"  # many panels return the base64 link list only to known clients


class SubscriptionError(Exception):
    """Fetch/parse failure; `code` selects the user-facing message."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ── Start port setting ────────────────────────────────────────

def get_start_port() -> int:
    try:
        port = int(settings_manager.get_setting(START_PORT_KEY, DEFAULT_START_PORT))
    except (TypeError, ValueError):
        return DEFAULT_START_PORT
    return port if MIN_START_PORT <= port <= MAX_START_PORT else DEFAULT_START_PORT


def parse_start_port(raw: Any) -> int:
    """Validate user input for the start port; raises ValueError with a clear range."""
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("not_a_number")
    if not MIN_START_PORT <= port <= MAX_START_PORT:
        raise ValueError("out_of_range")
    return port


def set_start_port(port: int) -> None:
    settings_manager.set_setting(START_PORT_KEY, parse_start_port(port))


# ── Fetch & decode ────────────────────────────────────────────

def validate_subscription_url(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise SubscriptionError("bad_url")
    return url


def _fetch_once(url: str, timeout: float, http_proxy: Optional[str]) -> bytes:
    handlers = [urllib.request.ProxyHandler({"http": http_proxy, "https": http_proxy} if http_proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise SubscriptionError("too_large")
    return body


def fetch_subscription(
    url: str,
    fallback_proxies: Sequence[str] = (),
    timeout: float = 20.0,
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[bytes, str]:
    """Download the subscription body. Tries a direct connection first, then each
    http://host:port proxy from `fallback_proxies`. Returns (body, route used)."""
    url = validate_subscription_url(url)
    routes: list[Optional[str]] = [None] + [p for p in fallback_proxies if p]
    errors = []
    for proxy in routes:
        route = proxy or "direct"
        try:
            body = _fetch_once(url, timeout, proxy)
            log(f"subscription fetched via {route} ({len(body)} bytes)")
            return body, route
        except SubscriptionError:
            raise
        except urllib.error.HTTPError as err:
            # The server answered: another route would get the same answer
            raise SubscriptionError("http_status", str(err.code))
        except Exception as err:
            log(f"subscription fetch via {route} failed: {err}")
            errors.append(f"{route}: {err}")
    raise SubscriptionError("network", "; ".join(errors))


def decode_subscription(body: bytes | str) -> list[str]:
    """Split a subscription body into share links. Accepts plain text or base64 (std/url-safe)."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    text = text.strip().lstrip("﻿")
    if "://" not in text:
        compact = "".join(text.split())
        compact += "=" * (-len(compact) % 4)
        decoded = None
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(compact.encode("ascii")).decode("utf-8", errors="replace")
                break
            except (binascii.Error, ValueError, UnicodeEncodeError):
                continue
        if decoded is None:
            raise SubscriptionError("bad_format")
        text = decoded
    return [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]


def _link_key(url: str) -> str:
    """Identity of a link for de-duplication: everything except the #remark."""
    return url.split("#", 1)[0].strip()


@dataclass
class ParsedSubscription:
    links: list[str] = field(default_factory=list)       # valid, new vless:// links
    duplicates: int = 0                                  # already present as a proxy (or repeated)
    unsupported: int = 0                                 # other protocols (vmess://, trojan://...)
    invalid: int = 0                                     # vless:// that does not parse


def extract_vless_links(lines: Iterable[str], existing_urls: Iterable[str] = ()) -> ParsedSubscription:
    result = ParsedSubscription()
    seen = {_link_key(u) for u in existing_urls if u}
    for line in lines:
        if not line.lower().startswith("vless://"):
            result.unsupported += 1
            continue
        try:
            parse_vless_url(line, strict=False)
        except ConfigError:
            result.invalid += 1
            continue
        key = _link_key(line)
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)
        result.links.append(line)
    return result


# ── Ports & proxy configs ─────────────────────────────────────

def allocate_ports(
    count: int,
    start: int,
    used_ports: Iterable[int],
    is_free: Callable[[int], bool],
) -> list[int]:
    """`count` ascending ports from `start`: not used by any proxy (as SOCKS or HTTP
    port), and free on the system together with their HTTP port (port + 10000)."""
    used = set(used_ports)
    taken = used | {p + HTTP_PORT_OFFSET for p in used} | {p - HTTP_PORT_OFFSET for p in used}
    ports: list[int] = []
    port = start
    while len(ports) < count and port <= MAX_START_PORT:
        http_port = port + HTTP_PORT_OFFSET
        if port not in taken and http_port not in taken and is_free(port) and is_free(http_port):
            ports.append(port)
            taken.update((port, http_port))
        port += 1
    if len(ports) < count:
        raise SubscriptionError("no_ports", str(count - len(ports)))
    return ports


def link_name(url: str) -> str:
    try:
        server = parse_vless_url(url, strict=False)
    except ConfigError:
        return ""
    return (server.remark or f"{server.address}:{server.port}").strip()[:60]


def build_proxy_config(
    base: dict[str, Any],
    url: str,
    port: int,
    order: float,
    group_id: Optional[str],
    subscription_id: str,
) -> dict[str, Any]:
    """A ready-to-start vless proxy entry for instances.json."""
    cfg = dict(base)
    for key in ("system_proxy", "System_Proxy", "work_proxy", "Work_Proxy",
                "telegram_proxy", "TelegramProxy", "fallbackProxy", "group", "groups"):
        cfg.pop(key, None)
    cfg.update({
        "name": link_name(url),
        "url": url,
        "listen": f"127.0.0.1:{port}",
        "order": int(order) if float(order).is_integer() else order,
        "system_proxy": False, "System_Proxy": False,
        "work_proxy": False, "Work_Proxy": False,
        "telegram_proxy": False, "TelegramProxy": False,
        SUB_CFG_KEY: subscription_id,
    })
    cfg.setdefault("username", "")
    cfg.setdefault("password", "")
    cfg.setdefault("udp", True)
    cfg.setdefault("connectTimeout", 10)
    cfg.setdefault("udpIdleTimeout", 60)
    cfg.setdefault("logLevel", "info")
    cfg.setdefault("backend", "auto")
    cfg.setdefault("xrayPath", "")
    cfg.setdefault("xrayLegacyConfig", False)
    # Not inherited from the config.json template: with killswitch on, every imported
    # proxy ran its own leak check right after start, dozens at once
    cfg["killswitch"] = False
    if group_id:
        cfg["groups"] = [group_id]
    return cfg


# ── Subscription records ──────────────────────────────────────

def load_subscriptions() -> list[dict[str, Any]]:
    raw = settings_manager.get_setting(SUBSCRIPTIONS_KEY, [])
    return [s for s in raw if isinstance(s, dict) and s.get("id")] if isinstance(raw, list) else []


def record_subscription(url: str, sub_type: str, group_id: Optional[str], added: int) -> dict[str, Any]:
    """Remember a subscription (re-adding the same URL updates its record)."""
    subs = load_subscriptions()
    rec = next((s for s in subs if s.get("url") == url), None)
    if rec is None:
        rec = {"id": f"sub_{uuid.uuid4().hex[:10]}", "url": url}
        subs.append(rec)
    rec.update({
        "type": sub_type,
        "group": group_id or "",
        "last_added": int(added),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    settings_manager.set_setting(SUBSCRIPTIONS_KEY, subs)
    return rec


def subscription_id_for(url: str) -> Optional[str]:
    return next((s["id"] for s in load_subscriptions() if s.get("url") == url), None)
