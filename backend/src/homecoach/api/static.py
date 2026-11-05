"""Serve the single-page application without exposing files outside its build directory."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_web(app: FastAPI, directory: Path) -> None:
    root = directory.resolve()
    if not (root / "index.html").is_file():
        return
    app.mount("/assets", StaticFiles(directory=root / "assets", check_dir=False), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(404, "file not found")
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "endpoint not found")
        return FileResponse(candidate if path and candidate.is_file() else root / "index.html")
