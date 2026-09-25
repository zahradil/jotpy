from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from jotpy.app import create_app


def cli_arg(name: str) -> str | None:
    prefix = f"--{name}="
    for arg in sys.argv[1:]:
        if arg.startswith(prefix):
            value = arg[len(prefix) :]
            return value or None
    return None


def main() -> None:
    port_text = cli_arg("port") or os.environ.get("PORT") or "3210"
    data_text = cli_arg("data") or os.environ.get("DATA_DIR") or str(Path.cwd() / "data")
    port = int(port_text)
    data_dir = Path(data_text)
    app = create_app(data_dir)
    resolved = app.state.runtime.data_dir

    class _Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            print(f"jot listening on http://localhost:{port}", flush=True)
            print(f"data: {resolved}", flush=True)

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        ws_ping_interval=30.0,
        ws_ping_timeout=30.0,
    )
    _Server(config).run()


if __name__ == "__main__":
    main()
