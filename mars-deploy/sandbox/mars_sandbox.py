"""OS sandbox wrapper for the coding agent on Linux and Windows, the counterpart of the macOS sandbox-exec profile.

Called by mars-core through MARS_SANDBOX_COMMAND (JSON argv, e.g. ["python", ".../mars_sandbox.py"]):

    mars_sandbox.py --write=DIR [--write=DIR ...] [--deny-read=PATH ...] --port=GATEWAY_PORT -- COMMAND ...

--deny-read names credential files or directories the agent must not read (model API key file, GPU SSH key, ~/.ssh).
bwrap shadows each with an empty file or tmpfs; Landlock switches to a read allow-list that covers everything except
them; Windows gives each a Medium no-read-up integrity label. A path that does not exist is skipped. If the chosen
backend cannot enforce a denial it refuses to start (exit 97) rather than leaving the file readable.

Linux, preferred: bubblewrap (no root needed when unprivileged user namespaces are allowed):
  * the whole file system is mounted read-only; only the --write directories are writable, /tmp and /run are private;
  * the agent gets its own network namespace with nothing but loopback. 127.0.0.1:GATEWAY_PORT inside is relayed
    over a unix socket to the real gateway, so that one port is the only reachable endpoint (no other local
    service, no intranet, no DNS).
Linux, fallback when bwrap is missing or blocked (e.g. Ubuntu 24.04 AppArmor userns restriction): Landlock
  (no root, kernel 6.7+ for the network rule): writes only beneath the --write directories, TCP connect only to the
  gateway port. Weaker than bwrap: the port is allowed on any address and UDP is not covered.
Windows (no administrator needed):
  * the agent runs at Low integrity level; the --write directories are labelled Low, so every other location
    (medium by default) is read-only for it and its children (Mandatory Integrity Control "no write up");
  * network isolation needs administrator rights on Windows (firewall / AppContainer loopback exemption), so it is
    NOT enforced. The wrapper refuses to start unless MARS_SANDBOX_WINDOWS_NETWORK=unrestricted acknowledges this.

The wrapper never pretends: if it cannot enforce what it promises it exits with code 97 and a reason on stderr.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

REFUSED = 97
INNER_SOCKET = "/tmp/.mars-gw/gw.sock"


def refuse(reason: str) -> int:
    sys.stderr.write(json.dumps({"mars_sandbox": "refused", "reason": reason}, ensure_ascii=False) + "\n")
    return REFUSED


def announce(**facts: object) -> None:
    sys.stderr.write(json.dumps({"mars_sandbox": "enforced", **facts}, ensure_ascii=False) + "\n")
    sys.stderr.flush()


# ---------------- TCP <-> unix-socket relay (both ends of the Linux network bridge) ----------------
async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except OSError:
        pass
    finally:
        with suppress(Exception):
            writer.close()


def start_relay(listen, connect) -> None:
    """Serve in a daemon thread; returns once listening. listen/connect are asyncio factories."""
    ready, failure = threading.Event(), []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await connect()
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))

    async def main() -> None:
        try:
            server = await listen(handle)
        except OSError as error:
            failure.append(error)
            ready.set()
            return
        ready.set()
        async with server:
            await server.serve_forever()

    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    ready.wait(30)
    if failure or not ready.is_set():
        raise RuntimeError(f"relay failed to listen: {failure[0] if failure else 'timeout'}")


def run_child(command: list[str], env: dict[str, str] | None = None) -> int:
    """Run with inherited stdio; termination signals are passed on so the whole sandbox ends with us."""
    child = subprocess.Popen(command, env=env)
    for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, lambda number, _frame: child.send_signal(number))
    return child.wait()


# ---------------- Linux ----------------
def linux(writes: list[str], deny: list[Path], port: int, command: list[str]) -> int:
    """bubblewrap when user namespaces work (strict network), otherwise Landlock; MARS_SANDBOX_LINUX=bwrap|landlock."""
    mode = os.environ.get("MARS_SANDBOX_LINUX", "auto")
    bwrap = os.environ.get("MARS_BWRAP") or shutil.which("bwrap")
    if not bwrap:
        why = "没有 bubblewrap（bwrap），可请管理员安装发行版的 bubblewrap 包"
    else:
        probe = subprocess.run([bwrap, "--unshare-all", "--ro-bind", "/", "/", "--", "/bin/true"], capture_output=True, text=True)
        why = "" if probe.returncode == 0 else ("bwrap 无法创建用户/命名空间（内核禁止非特权 user namespace，或 AppArmor "
                                                "未放行 bwrap，需要管理员处理）：" + probe.stderr.strip()[-200:])
    if mode != "landlock" and not why:
        return bubblewrap(bwrap, writes, deny, port, command)
    if mode == "bwrap":
        return refuse(why)
    return landlock(writes, deny, port, command, why)


def bubblewrap(bwrap: str, writes: list[str], deny: list[Path], port: int, command: list[str]) -> int:
    sockets = tempfile.mkdtemp(prefix="mars-gw-")  # short path: unix socket paths are limited to 108 bytes
    host_socket = os.path.join(sockets, "gw.sock")
    start_relay(lambda handle: asyncio.start_unix_server(handle, host_socket),
                lambda: asyncio.open_connection("127.0.0.1", port))
    args = [bwrap, "--die-with-parent", "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--tmpfs", "/dev/shm",
            "--proc", "/proc", "--tmpfs", "/run", "--tmpfs", "/tmp", "--bind", sockets, os.path.dirname(INNER_SOCKET)]
    for path in writes:
        args += ["--bind", path, path]
    empty = os.path.join(sockets, "empty")  # one empty file to shadow every denied regular file
    open(empty, "w").close()
    for path in deny:  # shadow last so these win over the read-only root: a dir becomes empty, a file becomes empty
        args += (["--tmpfs", str(path)] if path.is_dir() else ["--ro-bind", empty, str(path)])
    inner = [sys.executable, os.path.abspath(__file__), "--inner", f"--port={port}", "--", *command]
    args += ["--chdir", os.getcwd(), "--setenv", "TMPDIR", "/tmp", "--", *inner]
    announce(platform="linux", writable=writes, denied_read=[str(p) for p in deny],
             network=f"127.0.0.1:{port} only (relayed)", tool="bubblewrap")
    try:
        return run_child(args)
    finally:
        shutil.rmtree(sockets, ignore_errors=True)


def linux_inner(port: int, command: list[str]) -> int:
    """Inside the sandbox: listen on the private loopback and relay to the gateway socket, then run the agent."""
    start_relay(lambda handle: asyncio.start_server(handle, "127.0.0.1", port),
                lambda: asyncio.open_unix_connection(INNER_SOCKET))
    return run_child(command)


READ_FILE, READ_DIR = 1 << 2, 1 << 3


def read_carveout(deny: list[Path]) -> tuple[list[Path], set[Path]]:
    """Landlock only allows, never denies. To make everything readable EXCEPT the denied paths, grant read on every
    sibling along each denied path's ancestry: the denied leaf gets no rule, its siblings' whole subtrees do."""
    block: set[Path] = set()                       # the denied targets and all their ancestors (incl. "/")
    for target in deny:
        node = target
        while True:
            block.add(node)
            if node.parent == node:
                break
            node = node.parent
    allow: list[Path] = []
    for node in block:
        if node in deny or not node.is_dir():
            continue
        try:
            children = list(node.iterdir())
        except OSError:
            continue                               # a dir we cannot list stays unreadable; safe, never leaks
        allow += [child for child in children if child not in block]
    return allow, set(deny)


def landlock(writes: list[str], deny: list[Path], port: int, command: list[str], why_not_bwrap: str) -> int:
    """Kernel Landlock on this process, then exec the agent (inherited by every child). No root, no namespaces.
    Writes only beneath the --write directories (and to /dev devices); TCP connect only to the gateway port
    (ABI >= 4, kernel 6.7+; any address on that port), no TCP listening. With --deny-read, READ is handled and
    allow-listed everywhere except the denied paths (carve-out). UDP is not covered by Landlock."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    call = lambda number, *args: libc.syscall(ctypes.c_long(number), *args)  # noqa: E731
    abi = call(444, None, ctypes.c_size_t(0), ctypes.c_uint32(1))  # landlock_create_ruleset(VERSION)
    if abi < 1:
        return refuse(f"{why_not_bwrap}；内核也没有启用 Landlock（需要 5.13+ 且 lsm 含 landlock）")
    if abi < 4 and os.environ.get("MARS_SANDBOX_LINUX_NETWORK") != "unrestricted":
        return refuse(f"{why_not_bwrap}；Landlock ABI {abi} 不能限制网络（需要内核 6.7+）。确认接受“只限制写入”后设置 "
                      "MARS_SANDBOX_LINUX_NETWORK=unrestricted")
    write_fs = (1 << 1) | (1 << 4) | (1 << 5) | sum(1 << bit for bit in range(6, 13))  # WRITE_FILE, REMOVE_*, MAKE_*
    write_fs |= (1 << 13 if abi >= 2 else 0) | (1 << 14 if abi >= 3 else 0)        # REFER, TRUNCATE
    handled_net = 0b11 if abi >= 4 else 0                                              # BIND_TCP, CONNECT_TCP
    read_allow, denied = (read_carveout(deny) if deny else ([], set()))
    handled_fs = write_fs | (READ_FILE | READ_DIR if deny else 0)

    class Ruleset(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    class NetPort(ctypes.Structure):
        _fields_ = [("allowed", ctypes.c_uint64), ("port", ctypes.c_uint64)]

    ruleset = Ruleset(handled_fs, handled_net)
    fd = call(444, ctypes.byref(ruleset), ctypes.c_size_t(16 if abi >= 4 else 8), ctypes.c_uint32(0))
    if fd < 0:
        return refuse(f"landlock_create_ruleset 失败：errno {ctypes.get_errno()}")
    read_rw = (READ_FILE | READ_DIR) if deny else 0  # when read is handled, the write dirs and /dev need read too
    required = [(Path(path), write_fs | read_rw) for path in writes] + [(Path("/dev"), (1 << 1) | (1 << 14 if abi >= 3 else 0) | read_rw)]
    for path, allowed in required:
        parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
        added = call(445, ctypes.c_int(fd), ctypes.c_int(1), ctypes.byref(PathBeneath(allowed, parent)), ctypes.c_uint32(0))
        os.close(parent)
        if added != 0:
            return refuse(f"landlock_add_rule({path}) 失败：errno {ctypes.get_errno()}")
    for path in read_allow:  # carve-out siblings: a path we cannot open or stat just stays unreadable, never leaks
        try:
            allowed = (READ_FILE | READ_DIR) if path.is_dir() else READ_FILE
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            continue
        call(445, ctypes.c_int(fd), ctypes.c_int(1), ctypes.byref(PathBeneath(allowed, parent)), ctypes.c_uint32(0))
        os.close(parent)
    if handled_net and call(445, ctypes.c_int(fd), ctypes.c_int(2), ctypes.byref(NetPort(1 << 1, port)), ctypes.c_uint32(0)):
        return refuse(f"landlock 网络规则失败：errno {ctypes.get_errno()}")
    if libc.prctl(38, 1, 0, 0, 0) != 0 or call(446, ctypes.c_int(fd), ctypes.c_uint32(0)) != 0:  # NO_NEW_PRIVS; restrict_self
        return refuse(f"landlock_restrict_self 失败：errno {ctypes.get_errno()}")
    os.close(fd)
    tmp = Path(writes[-1]) / "tmp"
    tmp.mkdir(exist_ok=True)
    announce(platform="linux", tool=f"landlock ABI {abi}", writable=writes, fallback_reason=why_not_bwrap,
             denied_read=[str(p) for p in denied] if deny else "not handled (no --deny-read)",
             network=f"TCP connect only to port {port}; UDP not covered" if handled_net else "NOT restricted")
    os.execvpe(command[0], command, {**os.environ, "TMPDIR": str(tmp)})
    return REFUSED  # unreachable: exec replaced this process


# ---------------- Windows ----------------
def windows_no_read_up(path: Path) -> str:
    """Give one file or directory (recursively) a Medium integrity label with NoReadUp|NoWriteUp|NoExecuteUp, so a
    Low-integrity process cannot read it; the Medium kernel still can. Set through SetNamedSecurityInfoW with a SACL
    built from SDDL. Persists after the session (a deliberate hardening of the credential file); re-applying is a
    no-op. Returns "" on success or an error string."""
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetSecurityDescriptorSacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    psd = ctypes.c_void_p()
    # S: system ACL, ML = mandatory label, NRNWNX = no read/write/execute up, ME = Medium integrity level
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW("S:(ML;;NRNWNX;;;ME)", 1,
                                                                         ctypes.byref(psd), None):
        return f"SDDL 解析失败：{ctypes.WinError(ctypes.get_last_error())}"
    present, sacl, defaulted = wintypes.BOOL(), ctypes.c_void_p(), wintypes.BOOL()
    advapi32.GetSecurityDescriptorSacl(psd, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted))
    targets = [path, *(c for c in path.rglob("*") if not c.is_symlink())] if path.is_dir() else [path]
    try:
        for target in targets:  # SE_FILE_OBJECT=1, LABEL_SECURITY_INFORMATION=0x10
            error = advapi32.SetNamedSecurityInfoW(str(target), 1, 0x10, None, None, None, sacl)
            if error != 0:
                return f"{target}: SetNamedSecurityInfo 错误 {error}"
    finally:
        ctypes.windll.kernel32.LocalFree(psd)
    return ""


def windows(writes: list[str], deny: list[Path], port: int, command: list[str]) -> int:
    if os.environ.get("MARS_SANDBOX_WINDOWS_NETWORK") != "unrestricted":
        return refuse("Windows 普通用户无法限制编码 Agent 的网络（需要管理员配置防火墙或 AppContainer 回环豁免）。"
                      "确认接受“只限制写入、不限制网络”后设置 MARS_SANDBOX_WINDOWS_NETWORK=unrestricted")
    for path in deny:  # hide credentials from the Low agent before it starts; refuse rather than leave one readable
        error = windows_no_read_up(path)
        if error:
            return refuse(f"无法对凭据加不可读标签：{error}")
    for path in writes:  # Low label on the writable roots; everything else stays medium = read-only for the agent
        labelled = subprocess.run(["icacls", path, "/setintegritylevel", "(OI)(CI)low", "/T", "/C", "/Q"],
                                  capture_output=True)
        if labelled.returncode != 0:
            return refuse(f"无法把可写目录标记为低完整性：{path}（icacls 退出码 {labelled.returncode}）")
    home = Path(writes[-1])  # the last --write is the agent's runtime directory (mars-core passes worktree, runtime)
    env = {**os.environ, "TEMP": str(home / "tmp"), "TMP": str(home / "tmp"),
           "APPDATA": str(home / "home/AppData/Roaming"), "LOCALAPPDATA": str(home / "home/AppData/Local"),
           # not enforcement: only steers tools that honour proxy variables away from the network
           "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9",
           "NO_PROXY": "127.0.0.1,localhost"}
    for key in ("TEMP", "APPDATA", "LOCALAPPDATA"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    announce(platform="windows", writable=writes, integrity="low", denied_read=[str(p) for p in deny],
             network="NOT restricted (needs administrator)")
    return run_child([sys.executable, os.path.abspath(__file__), "--lower", "--", *command], env=env)


def windows_lower(command: list[str]) -> int:
    """Lower this process's own token to Low integrity, verify it, then run the agent (children inherit it)."""
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]

    class Label(ctypes.Structure):  # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label }
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    token, sid = wintypes.HANDLE(), ctypes.c_void_p()
    ok = (advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0080 | 0x0008, ctypes.byref(token))  # ADJUST_DEFAULT|QUERY
          and advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)))  # Low Mandatory Level
    if ok:
        label = Label(sid, 0x20)  # SE_GROUP_INTEGRITY
        ok = advapi32.SetTokenInformation(token, 25, ctypes.byref(label), ctypes.sizeof(label) + advapi32.GetLengthSid(sid))
    if not ok:
        return refuse(f"无法把进程降到低完整性级别：{ctypes.WinError(ctypes.get_last_error())}")
    buffer, needed = ctypes.create_string_buffer(64), wintypes.DWORD()
    if not advapi32.GetTokenInformation(token, 25, buffer, 64, ctypes.byref(needed)):
        return refuse(f"无法读回完整性级别：{ctypes.WinError(ctypes.get_last_error())}")
    level_sid = ctypes.cast(buffer, ctypes.POINTER(Label)).contents.Sid
    count = ctypes.cast(level_sid + 1, ctypes.POINTER(ctypes.c_ubyte)).contents.value  # SubAuthorityCount
    level = ctypes.cast(level_sid + 8 + 4 * (count - 1), ctypes.POINTER(wintypes.DWORD)).contents.value
    if level > 0x1000:
        return refuse(f"完整性级别未降到 Low（当前 0x{level:x}）")
    return run_child(command)


def main(argv: list[str]) -> int:
    if "--" not in argv:
        return refuse("用法：mars_sandbox.py --write=DIR ... --port=N -- COMMAND ...")
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog="mars_sandbox.py")
    parser.add_argument("--write", action="append", default=[])
    parser.add_argument("--deny-read", action="append", default=[])  # credential paths the agent must not read
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--inner", action="store_true")
    parser.add_argument("--lower", action="store_true")
    args, command = parser.parse_args(argv[:split]), argv[split + 1:]
    if not command:
        return refuse("缺少要运行的命令")
    if args.inner:
        return linux_inner(args.port, command)
    if args.lower:
        return windows_lower(command)
    writes = [str(Path(p).resolve()) for p in args.write]
    # Only existing paths can be hidden; a listed path that is absent needs no action (e.g. no ~/.ssh on this machine).
    # The launcher and the kernel may each list the same credential path, so de-duplicate (bwrap would double-mount).
    deny = [p.resolve() for p in (Path(x).expanduser() for x in args.deny_read) if p.exists()]
    deny = list(dict.fromkeys(deny))
    if not writes or not args.port:
        return refuse("至少需要一个 --write 目录和 --port")
    if sys.platform.startswith("linux"):
        return linux(writes, deny, args.port, command)
    if os.name == "nt":
        return windows(writes, deny, args.port, command)
    return refuse(f"不支持的平台 {sys.platform}；macOS 由内核直接使用 sandbox-exec")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
