"""Geo IP & Country Detection module for vlesstosocks5.

Provides:
- URL / Hash tag parser to extract server country, flag, and remark offline.
- Live SOCKS5 exit IP & Country resolution (via ip-api.com / ipwho.is).
- Country code to flag emoji conversion.
- Non-blocking background worker with thread-safe callbacks and memory cache.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import urllib.parse
from typing import Any, Callable, Optional

from vless2socks.socks_client import http_get_via_socks5

# Cache: key -> (timestamp, data)
_GEO_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 300.0  # 5 minutes cache

# Flag emoji map for common country codes or fallback conversion
def code_to_flag(code: str) -> str:
    """Convert ISO 3166-1 alpha-2 country code to emoji flag."""
    if not code or len(code) != 2:
        return "🌐"
    code = code.upper()
    try:
        return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))
    except Exception:
        return "🌐"


# Common country names from server host prefixes
DOMAIN_COUNTRY_MAP = {
    "fi": ("Finland", "🇫🇮"),
    "fin": ("Finland", "🇫🇮"),
    "de": ("Germany", "🇩🇪"),
    "ger": ("Germany", "🇩🇪"),
    "nl": ("Netherlands", "🇳🇱"),
    "us": ("United States", "🇺🇸"),
    "usa": ("United States", "🇺🇸"),
    "uk": ("United Kingdom", "🇬🇧"),
    "gb": ("United Kingdom", "🇬🇧"),
    "fr": ("France", "🇫🇷"),
    "se": ("Sweden", "🇸🇪"),
    "ch": ("Switzerland", "🇨🇭"),
    "pl": ("Poland", "🇵🇱"),
    "at": ("Austria", "🇦🇹"),
    "it": ("Italy", "🇮🇹"),
    "es": ("Spain", "🇪🇸"),
    "tr": ("Turkey", "🇹🇷"),
    "kz": ("Kazakhstan", "🇰🇿"),
    "ru": ("Russia", "🇷🇺"),
    "ua": ("Ukraine", "🇺🇦"),
    "sg": ("Singapore", "🇸🇬"),
    "jp": ("Japan", "🇯🇵"),
}

FLAG_RE = re.compile(r"([\U0001F1E6-\U0001F1FF]{2})")


def extract_country_hint(url: str) -> dict[str, str]:
    """Extract country, flag, and remark offline from VLESS URL hash or host."""
    res = {
        "country": "Unknown",
        "flag": "🌐",
        "remark": "",
        "host": "",
    }
    if not url:
        return res

    # 1. Parse remark / hash fragment (e.g., #🇫🇮 FINLAND 3 VLESS TCP)
    fragment = ""
    if "#" in url:
        raw_frag = url.split("#", 1)[1]
        try:
            fragment = urllib.parse.unquote(raw_frag).strip()
            res["remark"] = fragment
        except Exception:
            fragment = raw_frag

    # Look for emoji flag in fragment
    flag_match = FLAG_RE.search(fragment)
    if flag_match:
        res["flag"] = flag_match.group(1)

    # Look for country words in fragment
    upper_frag = fragment.upper()
    for short, (c_name, flag) in DOMAIN_COUNTRY_MAP.items():
        if c_name.upper() in upper_frag:
            res["country"] = c_name
            if res["flag"] == "🌐":
                res["flag"] = flag
            break

    # 2. Parse host domain if country still unknown
    try:
        after_at = url.split("@", 1)[1] if "@" in url else url
        hostport = after_at.split("?", 1)[0].split("#", 1)[0]
        host = hostport.split(":")[0].strip().lower()
        res["host"] = host

        if res["country"] == "Unknown":
            for prefix, (c_name, flag) in DOMAIN_COUNTRY_MAP.items():
                if host.startswith(prefix) or f".{prefix}." in host or host.endswith(f".{prefix}"):
                    res["country"] = c_name
                    if res["flag"] == "🌐":
                        res["flag"] = flag
                    break
    except Exception:
        pass

    return res


def fetch_geo_via_socks(
    socks_host: str = "127.0.0.1",
    socks_port: int = 1080,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Query external IP and Geo location through local SOCKS5 proxy synchronously."""
    cache_key = f"{socks_host}:{socks_port}"
    now = time.time()
    if cache_key in _GEO_CACHE:
        ts, data = _GEO_CACHE[cache_key]
        if now - ts < _CACHE_TTL and data.get("verified"):
            return dict(data)

    result = {
        "ip": "",
        "country": "",
        "country_code": "",
        "city": "",
        "flag": "🌐",
        "verified": False,
        "error": "",
    }

    # Primary API: http://ip-api.com/json (Port 80)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            status, body = loop.run_until_complete(
                http_get_via_socks5(
                    socks_host, socks_port,
                    host="ip-api.com", port=80, path="/json?fields=status,country,countryCode,city,query",
                    timeout=timeout,
                )
            )
            data = json.loads(body)
            if data.get("status") == "success":
                result["ip"] = data.get("query", "")
                result["country"] = data.get("country", "")
                result["country_code"] = data.get("countryCode", "")
                result["city"] = data.get("city", "")
                result["flag"] = code_to_flag(result["country_code"])
                result["verified"] = True
                _GEO_CACHE[cache_key] = (now, result)
                return result
        finally:
            loop.close()
    except Exception as e:
        err_msg = str(e)

    # Fallback API: http://ipwho.is/ (Port 80)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            status, body = loop.run_until_complete(
                http_get_via_socks5(
                    socks_host, socks_port,
                    host="ipwho.is", port=80, path="/",
                    timeout=timeout,
                )
            )
            data = json.loads(body)
            if data.get("success") is True or "country" in data:
                result["ip"] = data.get("ip", "")
                result["country"] = data.get("country", "")
                result["country_code"] = data.get("country_code", "")
                result["city"] = data.get("city", "")
                result["flag"] = code_to_flag(result["country_code"])
                result["verified"] = True
                _GEO_CACHE[cache_key] = (now, result)
                return result
        finally:
            loop.close()
    except Exception as e:
        err_msg = str(e)

    result["error"] = err_msg or "Failed to connect to geo API via SOCKS5"
    return result


def fetch_geo_async(
    socks_host: str,
    socks_port: int,
    callback: Callable[[dict[str, Any]], None],
    fallback_url: str = "",
) -> None:
    """Run geo lookup in background thread and call callback(result)."""
    def _worker():
        data = fetch_geo_via_socks(socks_host, socks_port)
        if not data.get("verified") and fallback_url:
            hint = extract_country_hint(fallback_url)
            if hint["country"] != "Unknown":
                data["country"] = hint["country"]
                data["flag"] = hint["flag"]
        try:
            callback(data)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
