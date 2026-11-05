"""Entry point: `homecoach <command>`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]  # backend/ (or /app in the image)
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="homecoach")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    api = sub.add_parser("api", help="run the web app and API")
    api.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container
    api.add_argument("--port", type=int, default=8080)
    sub.add_parser("collector", help="run the scheduled collector")
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
    elif args.cmd == "sync":
        from homecoach.collector.main import run_once

        run_once(full=args.full)


if __name__ == "__main__":
    main()
