"""Run the local API with a file-triggered graceful shutdown on Windows."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from pathlib import Path

import uvicorn


async def serve(port: int, shutdown_file: Path) -> None:
    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host="127.0.0.1", port=port, loop="asyncio", timeout_graceful_shutdown=15,
    ))

    async def watch() -> None:
        while not shutdown_file.exists():
            await asyncio.sleep(0.5)
        server.should_exit = True

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--shutdown-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.shutdown_file))
