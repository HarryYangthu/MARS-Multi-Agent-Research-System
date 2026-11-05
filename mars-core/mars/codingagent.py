"""Drive the official CodingAgent app-server (stdio NDJSON) as the coding executor inside a candidate checkout.

Uses isolated HOME and upstream storage settings with a
personal provider file make app-server use the configured OpenAI-compatible endpoint (the official headless mode
cannot select a model, zai-org/feedback#1014). CodingAgent works in the isolated candidate worktree with its own tools,
but in "build" mode: every permission request is decided by PermissionPolicy (paths inside allowed roots, commands
from an allowlist), and the model endpoint is the loopback gateway with a short-lived token, never the upstream key.
The engine checks scope and re-runs every measurement itself afterwards.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BUNDLE = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
TERMINAL_EVENTS = {"turn.completed", "turn.failed", "turn.stopped"}
SECRET_ENV = re.compile(r"API_?KEY|TOKEN|SECRET|PASSW(OR)?D|CREDENTIAL", re.I)  # never handed to the agent


def resolve_command() -> list[str]:
    if os.environ.get("MARS_CODINGAGENT_COMMAND"):
        return json.loads(os.environ["MARS_CODINGAGENT_COMMAND"])
    if shutil.which("zcode"):
        return [shutil.which("zcode")]  # type: ignore[list-item]
    node = shutil.which("node")
    if BUNDLE.is_file() and node:
        return [node, str(BUNDLE)]
    raise RuntimeError("CodingAgent 未安装：设置 MARS_CODINGAGENT_COMMAND 或安装 CodingAgent")


class PermissionPolicy:
    """Allow only actions whose paths stay inside allowed roots and whose shell commands match an allowlist."""

    DENY_TOKENS = ("sudo", "rm -rf /", "curl ", "wget ", "ssh ", "scp ", "git push", "git remote", "pip install",
                   "chmod 777", "mkfs", "> /", "~/.ssh", ".env")

    def __init__(self, roots: list[Path], command_prefixes: list[str]) -> None:
        self.roots = [r.resolve() for r in roots]
        self.prefixes = command_prefixes

    def _inside(self, raw: str) -> bool:
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            return False
        return any(path == root or root in path.parents for root in self.roots)

    def decide(self, params: Any) -> tuple[bool, str]:
        # Only the tool input is decisive; the request also echoes paths inside its approval options.
        if isinstance(params, dict) and isinstance(params.get("input"), dict):
            params = params["input"]
        command = next((s for k, s in _keyed_strings(params) if k in {"command", "cmd", "commandLine"}), "")
        for key, value in _keyed_strings(params):  # path-valued keys are checked even when the path contains spaces
            pathlike = key.lower().endswith("path") or key in {"cwd", "directory", "dir"}
            if _absolute(value) and len(value) > 1 and (pathlike or " " not in value) and not self._inside(value):
                return False, f"路径不在允许范围内：{value[:200]}"
        if command:
            lowered = command.strip()
            if any(token in lowered for token in self.DENY_TOKENS):
                return False, f"命令含禁止内容：{lowered[:200]}"
            if "$(" in lowered or "`" in lowered:
                return False, f"命令含命令替换：{lowered[:200]}"
            for segment in split_command(lowered):
                ok, reason = self._segment_ok(segment)
                if not ok:
                    return False, reason
        return True, "within policy"

    def _segment_ok(self, segment: str) -> tuple[bool, str]:
        """`cd <dir>` and `rm -f <files>` are allowed inside the roots; everything else must match the allowlist."""
        if not segment:
            return True, ""
        try:
            words = [w.strip("\"'") for w in shlex.split(segment, posix=os.name != "nt")]  # keep Windows backslashes
        except ValueError:  # unbalanced quotes after splitting a compound command
            words = segment.split()
        if words and words[0] == "cd":
            return (len(words) == 2 and self._inside(words[1]), f"cd 目标不在允许范围内：{segment[:200]}")
        if words and words[0] == "rm" and set(words[1:2]) <= {"-f"}:
            targets = [w for w in words[1:] if not w.startswith("-")]
            return (bool(targets) and all(not _absolute(w) and ".." not in w or self._inside(w) for w in targets),
                    f"删除目标不在允许范围内：{segment[:200]}")
        if any(segment.startswith(prefix) for prefix in self.prefixes):
            return True, ""
        return False, f"命令不在白名单：{segment[:200]}"


def _absolute(value: str) -> bool:
    return value.startswith(("/", "~", "\\")) or bool(re.match(r"[A-Za-z]:[\\/]", value))


def split_command(command: str) -> list[str]:
    """Split on ; | && || outside quotes, so every chained command is checked against the allowlist."""
    parts, buffer, quote, i = [], [], "", 0
    while i < len(command):
        char = command[i]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
            buffer.append(char)
        elif char in ";|&\n":
            parts.append("".join(buffer).strip())
            buffer = []
            while i + 1 < len(command) and command[i + 1] in "|&":
                i += 1
        else:
            buffer.append(char)
        i += 1
    parts.append("".join(buffer).strip())
    return [p for p in parts if p]


def _keyed_strings(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _keyed_strings(v, str(k))
    elif isinstance(value, list):
        for v in value:
            yield from _keyed_strings(v, key)
    elif isinstance(value, str):
        yield key, value


def secret_paths() -> list[Path]:
    """What the agent must not read (key variables are already filtered by SECRET_ENV): API-key env files and the model
    config naming them, the GPU private key, ~/.ssh, MARS_SECRET_PATHS (os.pathsep-separated)."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    found = [Path.home() / ".ssh"]
    models = Path(os.environ.get("MARS_MODELS_CONFIG") or root / "configs/models.yaml")
    if models.is_file():
        found.append(models)
        cfg = yaml.safe_load(models.read_text(encoding="utf-8")) or {}
        found += [(models.parent / p) for p in cfg.get("env_files", [])]
    from mars.project import machine_config
    key = (machine_config().get("remote") or {}).get("key")
    if key:
        key_path = Path(key).expanduser()
        key_path = key_path if key_path.is_absolute() else root / key_path
        found += [key_path, key_path.with_name(key_path.name + ".pub")]
        if key_path.parent.name == ".ssh":
            found.append(key_path.parent)
    found += [Path(p) for p in os.environ.get("MARS_SECRET_PATHS", "").split(os.pathsep) if p]
    return sorted({p.resolve() for p in found if p.exists()})


def sandbox_prefix(writable: list[Path], gateway_port: int) -> list[str]:
    """OS sandbox for the agent and its children: writes only to `writable`, no reads of secret_paths(), network only to
    the gateway. macOS: sandbox-exec; elsewhere MARS_SANDBOX_COMMAND (JSON argv) taking --write= --deny-read= --port= --,
    which refuses to start what it cannot enforce."""
    secrets = secret_paths()
    if os.environ.get("MARS_SANDBOX_COMMAND"):
        return [*json.loads(os.environ["MARS_SANDBOX_COMMAND"]), *(f"--write={p.resolve()}" for p in writable),
                *(f"--deny-read={p}" for p in secrets), f"--port={gateway_port}", "--"]
    if not Path("/usr/bin/sandbox-exec").exists():
        if os.environ.get("MARS_ALLOW_UNSANDBOXED_AGENT") == "1":
            return []
        raise RuntimeError("当前系统没有可用的编码 Agent 沙箱（macOS sandbox-exec）。请用 MARS_SANDBOX_COMMAND 配置等价的系统沙箱；"
                           "确需无沙箱运行时显式设置 MARS_ALLOW_UNSANDBOXED_AGENT=1")
    roots = " ".join(f'(subpath "{p.resolve()}")' for p in writable)
    denied = " ".join(f'({"subpath" if p.is_dir() else "literal"} "{p}")' for p in secrets)
    deny_read = f"(deny file-read* {denied})" if denied else ""
    profile = f"""(version 1)
(allow default)
(deny file-write*)
(allow file-write* {roots} (subpath "/private/var/folders") (subpath "/private/tmp") (subpath "/dev"))
{deny_read}
(deny network*)
(allow network* (local unix-socket))
(allow network-outbound (remote ip "localhost:{gateway_port}"))
"""
    return ["/usr/bin/sandbox-exec", "-p", profile]


def provider_config(model: str, endpoint: str, token: str, max_tokens: int, context_window: int) -> dict[str, Any]:
    """The only credential written for CodingAgent is the loopback gateway's short-lived token."""
    return {"schemaVersion": 1, "config": {
        "providerConfigRules": {"providerRules": [{
            "providerId": "mars", "providerName": "MARS", "enabled": True,
            "config": {"group": "standard-personal", "access": {"type": "api-key", "apiKey": token},
                       "api": {"type": "openai-chat-completions", "baseUrl": endpoint},
                       "personalModelIds": [model], "visibility": "visible"}}]},
        "modelConfigRules": {"manualProviderModelRules": [], "providerModelRules": [{
            "providerId": "mars", "modelId": model, "config": {"enabled": True,
                "properties": {"requiresMfjsToolSchema": False, "contextWindow": context_window,
                    "inputFormat": {"supportsText": True, "supportsImage": False, "supportsAudio": False,
                                    "supportsVideo": False, "supportsPdf": False},
                    "outputFormat": {"supportsText": True}, "supportsToolCall": True,
                    "supportsJsonSchemaOutput": False, "supportsNativeWebSearch": False,
                    "supportsMidConversationSystem": True},
                "optionSpecs": {"reasoningLevel": {"values": ["low"], "map": "{}"},
                                "maxOutputTokens": {"max": max_tokens, "map": '{"max_tokens": maxOutputTokens}'}}}}]},
        "defaultModelSelection": {"providerId": "mars", "modelId": model, "options": {"reasoningLevel": "low"}}}}


def runtime_environment(root: Path, provider: dict[str, Any]) -> dict[str, str]:
    """Private HOME and CodingAgent storage per session; personal plugins/skills/memory are disabled."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    home = root / "home"
    (home / ".zcode/cli").mkdir(parents=True, exist_ok=True)
    (home / ".zcode/cli/config.json").write_text(json.dumps({
        "plugins": {"enabled": False, "dirs": [], "enabledPlugins": {}}, "skills": {"enabled": False},
        "memory": {"use": False}, "features": {"subagent": False, "memory": False, "skill": False},
        "mcp": {"servers": {}}}), encoding="utf-8")
    builtin, personal = root / "builtin.json", root / "provider.json"
    builtin.write_text(json.dumps({"schemaVersion": 1, "revision": 1, "config": {
        "providerConfigRules": {"templateRules": [], "providerRules": []},
        "modelConfigRules": {k: [] for k in ("modelRules", "modelApiRules", "providerSiteRules",
                                             "templateModelRules", "builtinProviderModelRules")}}}), encoding="utf-8")
    personal.write_text(json.dumps(provider), encoding="utf-8")
    personal.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("ZCODE_") and k not in {"NODE_OPTIONS", "NODE_PATH"} and not SECRET_ENV.search(k)}
    return {**env, "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CACHE_HOME": str(home / ".cache"),
            "ZCODE_HOME": str(root), "ZCODE_DATA_BASE_DIR": str(root), "ZCODE_STORAGE_DIR": str(root / "storage"),
            "ZCODE_LOG_DIR": str(root / "logs"), "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(builtin),
            "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE": str(builtin),
            "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(personal), "ZCODE_MODEL_TELEMETRY_ENABLED": "false"}


@dataclass
class CodingAgentResult:
    status: str                      # completed | failed | stopped | timeout | error
    seconds: float
    final_text: str = ""
    error: str = ""
    events: int = 0
    tool_events: list[str] = field(default_factory=list)


class CodingAgentSession:
    """One app-server process + one session; send() may be called repeatedly for repair turns."""

    def __init__(self, workdir: Path, runtime_root: Path, endpoint: str, token: str, model: str,
                 policy: PermissionPolicy, log_path: Path, rpc_timeout: int = 60, max_tokens: int = 16000) -> None:
        self.workdir, self.runtime_root = workdir.resolve(), runtime_root
        self.endpoint, self.token, self.model, self.policy = endpoint, token, model, policy
        self.log_path, self.rpc_timeout, self.max_tokens = log_path, rpc_timeout, max_tokens
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.counter = 0
        self.session_id = ""
        self.denials: list[str] = []

    def _log(self, kind: str, payload: Any) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            text = json.dumps(payload, ensure_ascii=False)
            if len(text) > 20000:  # keep every line valid JSON
                payload = {"truncated": text[:20000]}
            handle.write(json.dumps({"ts": round(time.time(), 2), "kind": kind, "payload": payload}, ensure_ascii=False) + "\n")

    async def start(self) -> None:
        provider = provider_config(self.model, self.endpoint, self.token, self.max_tokens, 128000)
        env = runtime_environment(self.runtime_root, provider)
        port = int(self.endpoint.rsplit(":", 1)[1].split("/")[0])
        sandbox = sandbox_prefix([self.workdir, self.runtime_root], port)
        self._log("sandbox", {"enabled": bool(sandbox), "writable": [str(self.workdir), str(self.runtime_root)]})
        self.process = await asyncio.create_subprocess_exec(
            *sandbox, *resolve_command(), "app-server", cwd=self.workdir, env=env, limit=64 * 1024 * 1024,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        asyncio.create_task(self._read())
        asyncio.create_task(self._drain_stderr())
        await self.call("runtime/capabilities", {})
        result = await self.call("session/create", {
            "workspace": {"workspacePath": str(self.workdir), "workspaceKey": self.workdir.name},
            "mode": "build", "titleGenerationEnabled": False, "thoughtLevel": "low",
            "toolDenylist": [], "offPeakToolEnabled": False, "dynamicWorkflowEnabled": False,
            "model": {"providerId": "mars", "modelId": self.model, "options": {"reasoningLevel": "low"}}})
        self.session_id = _session_id(result)
        if not self.session_id:
            raise RuntimeError(f"CodingAgent session/create returned no session id: {str(result)[:500]}")
        await self.call("session/subscribe", {"sessionId": self.session_id,
                                              "deliveryKind": "web-remote-replayable", "includeSnapshot": False})

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        assert self.process is not None and self.process.stdin is not None
        self.counter += 1
        key = self.counter
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        self._log("call", {"id": key, "method": method, "params": params if method != "session/send" else {"chars": len(str(params))}})
        self.process.stdin.write(json.dumps({"id": key, "method": method, "params": params}).encode() + b"\n")
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, self.rpc_timeout)
        finally:
            self.pending.pop(key, None)

    async def _respond(self, key: Any, result: Any) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps({"id": key, "result": result}).encode() + b"\n")
        await self.process.stdin.drain()

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while line := await self.process.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            key = message.get("id")
            if "method" in message and key is not None:          # server → client request
                method = message["method"]
                self._log("server_request", message)
                if method == "session/requestRuntimePreferences":
                    await self._respond(key, {"nativeSearchEnhancementsEnabled": False, "memoryEnabled": False,
                                              "askUserQuestionAutoResolutionEnabled": True})
                elif method == "interaction/requestPermission":
                    try:
                        allowed, reason = self.policy.decide(message.get("params"))
                    except Exception as error:  # noqa: BLE001 - a policy bug must deny, never kill the session
                        allowed, reason = False, f"策略判断异常，按拒绝处理：{type(error).__name__}: {error}"
                    self._log("permission", {"allowed": allowed, "reason": reason})
                    if not allowed:
                        self.denials.append(reason)
                    await self._respond(key, {"decision": "allow" if allowed else "deny", "reason": reason})
                elif method == "interaction/requestUserInput":
                    await self._respond(key, {"cancelled": True})
                else:
                    await self._respond(key, {})
            elif key in self.pending:
                future = self.pending[key]
                if not future.done():
                    if "error" in message:
                        future.set_exception(RuntimeError(str(message["error"])[:800]))
                    else:
                        future.set_result(message.get("result"))
            else:
                self._log("event", message)
                await self.events.put(message)
        await self.events.put({"method": "mars/processExited", "params": {}})

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while chunk := await self.process.stderr.read(8192):
            self._log("stderr", chunk.decode(errors="replace")[-2000:])

    async def send(self, prompt: str, timeout: float) -> CodingAgentResult:
        started = time.monotonic()
        while not self.events.empty():
            self.events.get_nowait()
        await self.call("session/send", {"sessionId": self.session_id, "content": prompt,
                                         "modelSelection": {"providerId": "mars", "modelId": self.model,
                                                            "options": {"reasoningLevel": "low"}}})
        count, tools, last_text = 0, [], ""
        deadline = started + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with suppress(Exception):
                    await self.call("session/stop", {"sessionId": self.session_id})
                return CodingAgentResult("timeout", time.monotonic() - started, last_text, "active time exhausted", count, tools)
            try:
                event = await asyncio.wait_for(self.events.get(), remaining)
            except TimeoutError:
                continue
            count += 1
            if event.get("method") == "mars/processExited":
                return CodingAgentResult("error", time.monotonic() - started, last_text, "CodingAgent process exited", count, tools)
            params = event.get("params") or {}
            kind = str(params.get("type", "") or (params.get("payload") or {}).get("type", ""))
            text = _text_of(params)
            if text and ("assistant" in kind or kind.startswith("message")):
                last_text = text or last_text
            if "tool" in kind:
                tools.append(kind + ":" + str(params.get("name") or params.get("toolName") or "")[:60])
            if kind in TERMINAL_EVENTS:
                status = kind.split(".")[1]
                last_text = _text_of((params.get("payload") or {}).get("response", "")) or last_text
                return CodingAgentResult(status, time.monotonic() - started, last_text or await self.final_text(),
                                   "" if status == "completed" else json.dumps(params, ensure_ascii=False)[:800],
                                   count, tools)

    async def final_text(self) -> str:
        with suppress(Exception):
            result = await self.call("session/messages", {"sessionId": self.session_id})
            messages = result.get("messages", result) if isinstance(result, dict) else result
            for message in reversed(messages or []):
                if isinstance(message, dict) and message.get("role") == "assistant":
                    return _text_of(message)[:6000]
        return ""

    async def close(self) -> None:
        if self.process is None:
            return
        with suppress(Exception):
            if self.session_id:
                await asyncio.wait_for(self.call("session/stop", {"sessionId": self.session_id}), 3)
        self._terminate(signal.SIGTERM)
        with suppress(Exception):
            await asyncio.wait_for(self.process.wait(), 5)
        self._terminate(getattr(signal, "SIGKILL", signal.SIGTERM))
        (self.runtime_root / "provider.json").unlink(missing_ok=True)

    def _terminate(self, sig: int) -> None:
        assert self.process is not None
        if os.name == "nt":  # no process groups on Windows: end the app-server tree (sandbox wrapper, node, shells)
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
        else:
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, sig)


def _session_id(result: Any) -> str:
    if isinstance(result, dict):
        value = result.get("sessionId")
        if isinstance(value, str) and value.startswith("sess_"):
            return value
        for item in result.values():
            found = _session_id(item)
            if found:
                return found
    return ""


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "message", "delta"):
            if key in value:
                found = _text_of(value[key])
                if found:
                    return found
    if isinstance(value, list):
        return "".join(_text_of(v) for v in value)
    return ""


def summarize_log(path: Path) -> dict[str, Any]:
    """Aggregate one CodingAgent session log (codingagent.jsonl, written by CodingAgentSession): tool usage, tool errors, permission
    decisions, files written, time span. Telemetry and streamed text deltas are skipped."""
    tools: dict[str, int] = {}
    names: dict[str, str] = {}
    errors, approved, denied, touched, first, last = 0, 0, 0, set(), None, None
    with path.open(errors="replace", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            first, last = first or item.get("ts"), item.get("ts") or last
            kind, payload = item.get("kind"), item.get("payload")
            if kind == "permission" and isinstance(payload, dict):
                approved += bool(payload.get("allowed"))
                denied += not payload.get("allowed")
                continue
            if not isinstance(payload, dict) or payload.get("method") != "session/event":
                continue
            event = (payload.get("params") or {}).get("payload") or {}
            if event.get("kind") == "tool_input_start":
                names[event.get("toolCallId", "")] = event.get("toolName", "?")
            elif event.get("kind") == "tool_call":
                name = names.get(event.get("toolCallId", ""), "?")
                tools[name] = tools.get(name, 0) + 1
                target = (event.get("input") or {}).get("file_path")
                if target and name in ("Write", "Edit", "MultiEdit"):
                    touched.add(target)
            elif event.get("kind") == "tool_error":
                errors += 1
    return {"tools": tools, "tool_errors": errors, "permissions": {"approved": approved, "denied": denied},
            "files_touched": sorted(touched), "started": first, "ended": last}

