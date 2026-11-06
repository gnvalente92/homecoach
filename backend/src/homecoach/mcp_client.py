"""Minimal MCP client: streamable HTTP transport + OAuth 2.1 (discovery, dynamic client registration, PKCE).

Implements the parts of the Model Context Protocol a data collector needs: initialize, tools/list and
tools/call over HTTP POST (JSON or SSE responses, Mcp-Session-Id), and the MCP authorization flow:

1. Protected resource metadata (RFC 9728) -> authorization server
2. Authorization server metadata (RFC 8414 / OpenID discovery) -> endpoints
3. Dynamic client registration (RFC 7591)
4. Authorization code + PKCE (S256), with the `resource` parameter (RFC 8707)
5. Token refresh

State (client registration and tokens) is a plain dict that the caller persists.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

log = logging.getLogger(__name__)
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "homecoach", "version": "0.1"}


class MCPError(RuntimeError):
    pass


class AuthRequired(MCPError):
    """No valid token: the user needs to (re)connect."""


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def resolve_server_url(url: str, http: httpx.Client) -> str:
    """Follow redirects (e.g. mcp.coros.com -> regional host) without sending a body."""
    try:
        r = http.get(url, headers={"accept": "application/json, text/event-stream"}, follow_redirects=True)
        return str(r.url) if r.url else url
    except httpx.HTTPError:
        return url


def discover(server_url: str, http: httpx.Client) -> dict[str, Any]:
    """Find the authorization server endpoints for an MCP server."""
    origin, path = _origin(server_url), urlsplit(server_url).path.rstrip("/")
    resource_md = None
    for u in (f"{origin}/.well-known/oauth-protected-resource{path}", f"{origin}/.well-known/oauth-protected-resource"):
        r = http.get(u)
        if r.status_code == 200:
            resource_md = r.json()
            break
    if resource_md is None:  # fall back to the WWW-Authenticate hint on an unauthenticated request
        r = http.post(
            server_url,
            json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
            headers={"accept": "application/json, text/event-stream"},
        )
        hint = r.headers.get("www-authenticate", "")
        if "resource_metadata=" in hint:
            u = hint.split("resource_metadata=", 1)[1].split(",")[0].strip().strip('"')
            resource_md = http.get(u).json()
    issuer = (resource_md or {}).get("authorization_servers", [origin])[0].rstrip("/")
    as_origin, as_path = _origin(issuer), urlsplit(issuer).path.rstrip("/")
    as_md = None
    for u in (
        f"{as_origin}/.well-known/oauth-authorization-server{as_path}",
        f"{as_origin}/.well-known/openid-configuration{as_path}",
        f"{issuer}/.well-known/openid-configuration",
        f"{as_origin}/.well-known/oauth-authorization-server",
    ):
        r = http.get(u)
        if r.status_code == 200:
            as_md = r.json()
            break
    if not as_md or not as_md.get("authorization_endpoint") or not as_md.get("token_endpoint"):
        raise MCPError(f"couldn't find OAuth metadata for {server_url} (issuer {issuer})")
    return {
        "resource": (resource_md or {}).get("resource") or server_url,
        "issuer": issuer,
        "authorization_endpoint": as_md["authorization_endpoint"],
        "token_endpoint": as_md["token_endpoint"],
        "registration_endpoint": as_md.get("registration_endpoint"),
        "scopes": (resource_md or {}).get("scopes_supported") or as_md.get("scopes_supported") or [],
    }


def register(meta: dict, redirect_uris: list[str], http: httpx.Client) -> dict[str, Any]:
    if not meta.get("registration_endpoint"):
        raise MCPError("the COROS authorization server doesn't offer dynamic client registration")
    body = {
        "client_name": "homecoach (self-hosted)",
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if meta.get("scopes"):
        body["scope"] = " ".join(meta["scopes"])
    r = http.post(meta["registration_endpoint"], json=body)
    if r.status_code >= 400:
        raise MCPError(f"client registration was refused ({r.status_code}): {r.text[:300]}")
    reg = r.json()
    return {
        "client_id": reg["client_id"],
        "client_secret": reg.get("client_secret"),
        "redirect_uris": reg.get("redirect_uris", redirect_uris),
    }


def authorization_url(meta: dict, client: dict, redirect_uri: str) -> tuple[str, dict]:
    """Returns (url to send the browser to, pending state to keep until the callback)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    q = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": meta["resource"],
    }
    if meta.get("scopes"):
        q["scope"] = " ".join(meta["scopes"])
    sep = "&" if "?" in meta["authorization_endpoint"] else "?"
    return meta["authorization_endpoint"] + sep + urlencode(q), {
        "state": state,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
    }


def _token_request(meta: dict, client: dict, data: dict, http: httpx.Client) -> dict:
    data = {**data, "client_id": client["client_id"], "resource": meta["resource"]}
    secret = client.get("client_secret")
    headers = {"accept": "application/json"}
    if secret:  # confidential client: HTTP basic auth; public clients (PKCE) send only client_id
        r = http.post(meta["token_endpoint"], data=data, headers=headers, auth=httpx.BasicAuth(client["client_id"], secret))
    else:
        r = http.post(meta["token_endpoint"], data=data, headers=headers)
    if r.status_code >= 400:
        raise AuthRequired(f"token request failed ({r.status_code}): {r.text[:300]}")
    t = r.json()
    if "access_token" not in t:
        raise AuthRequired(f"no access_token in token response: {list(t)}")
    t["expires_at"] = time.time() + float(t.get("expires_in") or 3600) - 60
    return t


def exchange_code(meta: dict, client: dict, pending: dict, code: str, http: httpx.Client) -> dict:
    return _token_request(
        meta,
        client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "code_verifier": pending["verifier"],
        },
        http,
    )


def refresh(meta: dict, client: dict, tokens: dict, http: httpx.Client) -> dict:
    if not tokens.get("refresh_token"):
        raise AuthRequired("the COROS connection expired and has no refresh token: connect again")
    new = _token_request(meta, client, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}, http)
    new.setdefault("refresh_token", tokens["refresh_token"])  # some servers don't rotate it
    return new


class Session:
    """A JSON-RPC session with one MCP server. `state` must contain server_url, meta, client, tokens.
    Token refreshes update `state` in place; the caller saves it afterwards."""

    def __init__(self, state: dict, http: httpx.Client | None = None):
        self.state = state
        self.http = http or httpx.Client(timeout=60, follow_redirects=True)
        self.session_id: str | None = None
        self._id = 0
        self.tools: dict[str, dict] = {}

    # ---- transport ----
    def _headers(self) -> dict[str, str]:
        tok = self.state.get("tokens") or {}
        if not tok.get("access_token"):
            raise AuthRequired("not connected to COROS yet")
        if tok.get("expires_at", 0) < time.time():
            self._refresh()
        h = {
            "authorization": f"Bearer {self.state['tokens']['access_token']}",
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-protocol-version": PROTOCOL_VERSION,
        }
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        return h

    def _refresh(self) -> None:
        self.state["tokens"] = refresh(self.state["meta"], self.state["client"], self.state["tokens"], self.http)
        self.state["changed"] = True

    def _post(self, payload: dict, retry: bool = True) -> dict | None:
        r = self.http.post(self.state["server_url"], json=payload, headers=self._headers())
        if r.status_code == 401 and retry:
            self._refresh()
            return self._post(payload, retry=False)
        if r.status_code == 404 and self.session_id and retry:  # session expired on the server
            self.session_id = None
            self.initialize()
            return self._post(payload, retry=False)
        if r.status_code >= 400:
            raise MCPError(f"MCP server error {r.status_code}: {r.text[:300]}")
        if r.headers.get("mcp-session-id"):
            self.session_id = r.headers["mcp-session-id"]
        if "id" not in payload or r.status_code == 202 or not r.content:
            return None
        return _parse_response(r, payload["id"])

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        self._id += 1
        msg = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        if msg is None:
            raise MCPError(f"no response to {method}")
        if "error" in msg:
            raise MCPError(f"{method} failed: {msg['error'].get('message')}")
        return msg.get("result")

    # ---- protocol ----
    def initialize(self) -> dict:
        res = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def list_tools(self) -> dict[str, dict]:
        if not self.session_id:
            self.initialize()
        tools, cursor = [], None
        while True:
            res = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        self.tools = {t["name"]: t for t in tools}
        return self.tools

    def call(self, name: str, args: dict | None = None) -> str:
        """Call a tool and return its text content."""
        if not self.tools:
            self.list_tools()
        res = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        text = "\n".join(_unquote(c.get("text", "")) for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise MCPError(f"{name}: {text[:300]}")
        if not text and res.get("structuredContent") is not None:
            text = json.dumps(res["structuredContent"])
        return text


def _unquote(text: str) -> str:
    """Some servers (COROS) put a JSON-encoded string in the text block: '"Report\\nline 2"'. Decode it so the
    report has real line breaks."""
    s = text.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            v = json.loads(s)
        except json.JSONDecodeError:
            return text
        if isinstance(v, str):
            text = v
    return text.replace("\r\n", "\n")


def _parse_response(r: httpx.Response, want_id: int) -> dict:
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for block in r.text.split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        raise MCPError("no matching response in the event stream")
    msg = r.json()
    if isinstance(msg, list):
        msg = next((m for m in msg if m.get("id") == want_id), {})
    return msg
