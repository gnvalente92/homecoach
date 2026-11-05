"""Self-hosted web search (SearXNG) and page reading for the race assistant."""

from __future__ import annotations

import html
import json
import re
from typing import Any

import httpx

from homecoach.config import Settings

UA = "homecoach/0.1 (self-hosted training app)"
MAX_PAGE_CHARS = 4000


def search(s: Settings, query: str, n: int = 6, client: httpx.Client | None = None) -> list[dict[str, str]]:
    http = client or httpx.Client(timeout=20, headers={"user-agent": UA})
    r = http.get(s.searxng_url.rstrip("/") + "/search", params={"q": query, "format": "json"})
    if r.status_code == 403:
        raise RuntimeError("SearXNG refused JSON output: enable 'json' under search.formats in its settings.yml")
    r.raise_for_status()
    out = []
    for item in (r.json().get("results") or [])[:n]:
        out.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": (item.get("content") or "")[:300],
                "date": item.get("publishedDate") or "",
            }
        )
    return out


def fetch_page(url: str, client: httpx.Client | None = None) -> str:
    """Readable text of a page, with any schema.org Event data first (race sites often publish it)."""
    if not re.match(r"^https?://", url):
        raise ValueError("url must start with http:// or https://")
    http = client or httpx.Client(timeout=20, follow_redirects=True, headers={"user-agent": UA})
    r = http.get(url)
    r.raise_for_status()
    raw = r.text
    events = _json_ld_events(raw)
    text = _visible_text(raw)
    head = ("Structured event data: " + json.dumps(events, ensure_ascii=False)[:1500] + "\n\n") if events else ""
    return (head + text)[:MAX_PAGE_CHARS]


def _json_ld_events(raw: str) -> list[dict[str, Any]]:
    out = []
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", raw, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and "Event" in str(it.get("@type", "")):
                out.append(
                    {k: it.get(k) for k in ("name", "startDate", "endDate", "location", "description", "url") if it.get(k)}
                )
    return out


def _visible_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) > 2)
