"""Bind an owned loopback socket before starting the real MARS ASGI service."""
from __future__ import annotations

import json
import os
import socket

import uvicorn


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        os.environ["BACKEND_HOST"] = "127.0.0.1"
        os.environ["BACKEND_PORT"] = str(port)
        record = {"port": port, "pid": os.getpid()}
        os.write(1, ("MARS_DESKTOP_READY " + json.dumps(record) + "\n").encode())
        server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port, access_log=False))
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
