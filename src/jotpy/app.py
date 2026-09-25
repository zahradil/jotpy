from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from jotpy.routes import register_error_handlers, register_routes
from jotpy.runtime import Runtime

mimetypes.add_type("text/javascript", ".mjs")


def project_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], here.parents[1], here.parent):
        if (candidate / "public").is_dir():
            return candidate
    return here.parents[2]


def create_app(data_dir: str | Path) -> FastAPI:
    runtime = Runtime.open(Path(data_dir))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.runtime = runtime
    root = project_root()
    app.mount("/static/mermaid", StaticFiles(directory=str(root / "vendor" / "mermaid")), name="mermaid")
    app.mount("/static", StaticFiles(directory=str(root / "public")), name="static")
    register_routes(app)
    register_error_handlers(app)
    return app
