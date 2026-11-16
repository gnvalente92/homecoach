"""Entry point: `homecoach <command>`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()  # no ini file needed: migrations ship inside the package
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    command.upgrade(cfg, "head")


def _coros_check() -> None:
    """Walk through the official COROS connection step by step and say where it fails."""
    from datetime import date, timedelta

    import httpx

    from homecoach import coros_auth, mcp_client
    from homecoach.config import get_settings
    from homecoach.db import session_scope
    from homecoach.providers.coros_mcp import TOOLS, CorosMCPProvider

    s = get_settings()
    print(f"provider={s.provider} server={s.coros_mcp_url}")
    if s.provider != "coros-mcp":
        print("[note] HC_PROVIDER is not 'coros-mcp', so the collector won't use this connection yet")
    http = httpx.Client(timeout=30, follow_redirects=True)
    try:
        url = mcp_client.resolve_server_url(s.coros_mcp_url, http)
        meta = mcp_client.discover(url, http)
        print(f"[ok] discovery: server {url}, login at {meta['authorization_endpoint']}")
        print(f"     dynamic registration: {'yes' if meta.get('registration_endpoint') else 'NO (connect will fail)'}")
    except Exception as e:
        print(f"[FAIL] discovery: {e}")
        print("       check that this server can reach the COROS MCP address above (HC_COROS_MCP_URL)")
        raise SystemExit(1) from e
    with session_scope() as db:
        st = coros_auth.status(db)
        if not st["connected"]:
            print("[--] not connected yet: open homecoach and click 'Connect COROS', then run this again")
            raise SystemExit(0 if meta.get("registration_endpoint") else 1)
        print(f"[ok] connected since {st['connected_at']}")
        try:
            sess = coros_auth.session(db, http)
            tools = sess.list_tools()
            print(f"[ok] {len(tools)} tools: {', '.join(sorted(tools))}")
        except Exception as e:
            print(f"[FAIL] MCP session: {e}")
            raise SystemExit(1) from e
        p = CorosMCPProvider(sess, s.timezone)
        end = date.today()
        start = end - timedelta(days=6)
        ok = True
        for kind in TOOLS:
            if kind == "fit_urls":
                continue
            tool = p._tool(kind)
            if not tool:
                print(f"[--] {kind}: no tool on the server")
                continue
            try:
                if kind == "activities":
                    n = len(p.fetch_activities(start - timedelta(days=21), end))
                    print(f"[ok] {kind} ({tool}): {n} activities in 4 weeks")
                elif kind == "fitness":
                    f = p.fetch_fitness()
                    print(f"[ok] {kind} ({tool}): VO2max {f.vo2max if f else '?'}, predictions {f.predictions if f else None}")
                else:
                    text = p._text(kind, start, end)
                    print(f"[ok] {kind} ({tool}): {len(text)} chars")
            except Exception as e:
                ok = False
                print(f"[FAIL] {kind} ({tool}): {e}")
        days = p.fetch_daily(start, end)
        fields = sorted({k for d in days for k in d.values})
        print(f"[ok] daily metrics for {len(days)} days: {', '.join(fields)}")
        if sess.state.pop("changed", None):
            coros_auth.save(db, sess.state)
    raise SystemExit(0 if ok else 1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="homecoach")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    api = sub.add_parser("api", help="run the web app and API")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8080)
    sub.add_parser("collector", help="run the scheduled collector")
    sub.add_parser("coros-check", help="test the official COROS connection and every data report")
    once = sub.add_parser("sync", help="run one sync now")
    once.add_argument("--full", action="store_true")
    args = p.parse_args()

    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "api":
        import uvicorn

        _migrate()
        uvicorn.run("homecoach.api.main:app", host=args.host, port=args.port, proxy_headers=True)
    elif args.cmd == "collector":
        from homecoach.collector.main import main as collector_main

        collector_main()
    elif args.cmd == "coros-check":
        _coros_check()
    elif args.cmd == "sync":
        from homecoach.collector.main import run_once

        run_once(full=args.full)


if __name__ == "__main__":
    main()
