"""Self-hosted web search (SearXNG) and page reading for the race assistant."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from contextlib import nullcontext
from typing import Any

import httpx

from homecoach.config import Settings

UA = "homecoach/0.1 (self-hosted training app)"
MAX_PAGE_CHARS = 4000
MAX_PAGE_BYTES = 1_000_000


def _public_address(url: httpx.URL) -> str:
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise ValueError("use an http(s) URL without credentials")
    if url.port not in (None, 80, 443):
        raise ValueError("race pages must use a standard HTTP(S) port")
    try:
        addresses = [str(row[4][0]) for row in socket.getaddrinfo(url.host, url.port or 443, type=socket.SOCK_STREAM)]
    except OSError as e:
        raise ValueError("page hostname could not be resolved") from e
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise ValueError("race pages must use public internet addresses")
    return addresses[0]


def search(s: Settings, query: str, n: int = 6, client: httpx.Client | None = None) -> list[dict[str, str]]:
    http = client or httpx.Client(timeout=20, headers={"user-agent": UA})
    r = http.get(s.searxng_url.rstrip("/") + "/search", params={"q": query, "format": "json"})
    if r.status_code == 403:
        raise RuntimeError("SearXNG refused JSON output: enable 'json' under search.formats in its settings.yml")
    r.raise_for_status()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:300],
            "date": item.get("publishedDate") or "",
        }
        for item in (r.json().get("results") or [])[:n]
    ]


def fetch_page(url: str, client: httpx.Client | None = None) -> str:
    """Readable text of a page, with any schema.org Event data first (race sites often publish it)."""
    page = httpx.URL(url)
    # Pin each connection to its validated address. Keep the original Host and TLS name;
    # resolving once more while connecting would permit DNS rebinding.
    with nullcontext(client) if client is not None else httpx.Client(timeout=20, trust_env=False) as http:
        for redirect in range(6):
            address = _public_address(page)
            target = page if client is not None else page.copy_with(host=address)
            with http.stream(
                "GET",
                target,
                follow_redirects=False,
                headers={"user-agent": UA, "host": page.netloc.decode("ascii")},
                extensions={"sni_hostname": page.host},
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirect == 5 or "location" not in response.headers:
                        raise ValueError("page has too many redirects or no redirect destination")
                    page = page.join(response.headers["location"])
                    continue
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=16_384):
                    content.extend(chunk)
                    if len(content) > MAX_PAGE_BYTES:
                        raise ValueError("page is too large to read")
                raw = content.decode(response.encoding or "utf-8", errors="replace")
                break
    events = _json_ld_events(raw)
    text = _visible_text(raw)
    head = ("Structured event data: " + json.dumps(events, ensure_ascii=False)[:1500] + "\n\n") if events else ""
    return (head + text)[:MAX_PAGE_CHARS]


def _json_ld_events(raw: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", raw, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        out.extend(
            {k: it.get(k) for k in ("name", "startDate", "endDate", "location", "description", "url") if it.get(k)}
            for it in items
            if isinstance(it, dict) and "Event" in str(it.get("@type", ""))
        )
    return out


def _visible_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) > 2)
