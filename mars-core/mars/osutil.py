"""POSIX/Windows differences in one place: advisory file locks (fcntl.flock / msvcrt.locking) and process liveness."""
from __future__ import annotations

import os
import time
from typing import IO, Any

if os.name == "nt":
    import msvcrt

    _FAR = 1 << 30  # lock one byte far past the pid text, so other processes can still read the file

    def lock(handle: IO[Any], blocking: bool = False, shared: bool = False) -> None:
        """Exclusive on Windows (no shared byte-range locks); raises BlockingIOError when held elsewhere."""
        while True:
            handle.seek(_FAR)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if not blocking:
                    raise BlockingIOError("locked by another process") from None
                time.sleep(1)
            finally:
                handle.seek(0)

    def unlock(handle: IO[Any]) -> None:
        handle.seek(_FAR)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.seek(0)

    def alive(pid: int) -> bool:
        """Never os.kill(pid, 0) on Windows: signal 0 is CTRL_C_EVENT and would interrupt the process group."""
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists but belongs to someone else
        code = ctypes.c_ulong()
        try:
            return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
else:
    import fcntl

    def lock(handle: IO[Any], blocking: bool = False, shared: bool = False) -> None:
        fcntl.flock(handle, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | (0 if blocking else fcntl.LOCK_NB))

    def unlock(handle: IO[Any]) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)

    def alive(pid: int) -> bool:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # it exists but belongs to someone else
            return True
        return True
