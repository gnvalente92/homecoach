"""Connect homecoach to the official COROS MCP server and keep the connection's state in the database."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy.orm import Session as Db

from homecoach import mcp_client
from homecoach.config import Settings
from homecoach.models import SyncState

KEY = "coros-mcp-auth"


def load(db: Db) -> dict:
    st = db.get(SyncState, KEY)
    return dict(st.value) if st else {}


def save(db: Db, state: dict) -> None:
    st = db.get(SyncState, KEY) or SyncState(key=KEY, value={})
    st.value = {k: v for k, v in state.items() if k != "changed"}
    db.add(st)


def status(db: Db) -> dict:
    st = load(db)
    tokens = st.get("tokens") or {}
    return {
        "connected": bool(tokens.get("access_token")),
        "server": st.get("server_url"),
        "connected_at": st.get("connected_at"),
        "error": st.get("last_error"),
    }


def start(
    s: Settings, db: Db, redirect_uri: str, fallback_uri: str, use_fallback: bool = False, http: httpx.Client | None = None
) -> str:
    """Discover, register (both redirect addresses, once) and return the COROS login URL.
    use_fallback: send the browser back to the fallback address (which won't load) so the user pastes it."""
    http = http or httpx.Client(timeout=30, follow_redirects=True)
    st = load(db)
    server = mcp_client.resolve_server_url(s.coros_mcp_url, http)
    meta = mcp_client.discover(server, http)
    uris = sorted({redirect_uri, fallback_uri})
    client = st.get("client")
    if not client or sorted(client.get("redirect_uris", [])) != uris or st.get("meta", {}).get("issuer") != meta["issuer"]:
        client = mcp_client.register(meta, uris, http)
    url, pending = mcp_client.authorization_url(meta, client, fallback_uri if use_fallback else redirect_uri)
    st.update({"server_url": server, "meta": meta, "client": client, "pending": pending})
    save(db, st)
    return url


def finish(db: Db, code: str, state: str, http: httpx.Client | None = None) -> None:
    http = http or httpx.Client(timeout=30, follow_redirects=True)
    st = load(db)
    pending = st.get("pending") or {}
    if not pending or state != pending.get("state"):
        raise mcp_client.MCPError("this login link is stale or was already used: click Connect COROS again")
    st["tokens"] = mcp_client.exchange_code(st["meta"], st["client"], pending, code, http)
    st.pop("pending", None)
    st.pop("last_error", None)
    st["connected_at"] = datetime.now().isoformat(timespec="seconds")
    save(db, st)


def finish_from_pasted_url(db: Db, pasted: str, http: httpx.Client | None = None) -> None:
    """Fallback when the browser couldn't load the redirect page: the user pastes its address."""
    q = parse_qs(urlsplit(pasted.strip()).query)
    if "error" in q:
        raise mcp_client.MCPError(f"COROS returned an error: {q.get('error_description', q['error'])[0]}")
    if "code" not in q or "state" not in q:
        raise mcp_client.MCPError("that address has no code= and state= in it; copy the full address from the browser bar")
    finish(db, q["code"][0], q["state"][0], http)


def session(db: Db, http: httpx.Client | None = None) -> mcp_client.Session:
    st = load(db)
    if not (st.get("tokens") or {}).get("access_token"):
        raise mcp_client.AuthRequired("COROS isn't connected yet: open homecoach and click Connect COROS")
    return mcp_client.Session(st, http)


def disconnect(db: Db) -> None:
    st = load(db)
    for k in ("tokens", "pending", "connected_at"):
        st.pop(k, None)
    save(db, st)
