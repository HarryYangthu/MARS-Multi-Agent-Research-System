"""Verify the real bundled CLI protocol without making a model request."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import tempfile

from app.harness.agent_loop.zcode.config import ZCodeConfig, runtime_environment
from app.harness.agent_loop.zcode.protocol import ZCodeClient


async def probe() -> None:
    config = ZCodeConfig.load()
    with tempfile.TemporaryDirectory(prefix="mars-zcode-probe-") as directory:
        root = Path(directory)
        environment = runtime_environment(root, provider={"schemaVersion": 1, "config": {}})
        client = ZCodeClient(command=config.resolve_command(), cwd=root, env=environment,
            timeout=config.rpc_timeout_seconds, max_line_bytes=config.max_protocol_line_bytes)
        try:
            await client.start()
            capabilities = await client.call("runtime/capabilities", {})
            if not isinstance(capabilities, dict) or not capabilities:
                raise ValueError("Bundled ZCode did not return its real capabilities")
            sys.stdout.write(json.dumps({"zcode_protocol_ready": True, "model_requests": 0}) + "\n")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(probe())
