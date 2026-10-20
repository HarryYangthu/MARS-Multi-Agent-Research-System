"""Owned command groups: POSIX sessions or Windows Job Objects.

Windows commands start suspended, enter a kill-on-close Job Object, and only
then resume. Descendants cannot escape cleanup merely by outliving the leader.
This controls trusted process lifetime; it is not an OS security sandbox.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
from typing import Any


def worker_process_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": 0x00000200 | 0x00000008}  # NEW_PROCESS_GROUP | DETACHED_PROCESS
    return {"start_new_session": True}


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


class OwnedProcessGroup:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.kernel: Any = None
        self.job: Any = None
        if os.name != "nt":
            return
        self.kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.job = self.kernel.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("Could not create an owned Windows Job Object")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.kernel.CloseHandle(self.job)
            self.job = None
            raise OSError("Could not enable Windows descendant cleanup")

    def _attach_and_resume(self, process: subprocess.Popen[bytes]) -> None:
        handle = self.kernel.OpenProcess(0x0100 | 0x0001 | 0x0400, False, process.pid)
        if not handle:
            raise OSError("Could not open the owned suspended command")
        try:
            if not self.kernel.AssignProcessToJobObject(self.job, handle):
                raise OSError("Could not attach the owned command to its Windows Job Object")
        finally:
            self.kernel.CloseHandle(handle)
        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError("Could not inspect the owned command's startup thread")
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self.kernel.OpenThread(2, False, entry.th32ThreadID)
                    if not thread:
                        raise OSError("Could not open the owned startup thread")
                    try:
                        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise OSError("Could not resume the owned startup thread")
                        return
                    finally:
                        self.kernel.CloseHandle(thread)
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Owned suspended command has no startup thread")
        finally:
            self.kernel.CloseHandle(snapshot)

    def start(self, argv: tuple[str, ...], *, cwd: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
        options: dict[str, Any] = ({"creationflags": 0x00000200 | 0x00000004}  # NEW_PROCESS_GROUP | SUSPENDED
                                 if os.name == "nt" else {"start_new_session": True})
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, **options)
        self.process = process
        if os.name == "nt":
            try:
                self._attach_and_resume(process)
            except BaseException:
                process.kill()
                process.wait()
                self.stop()
                raise
        return process

    def stop(self) -> None:
        if os.name == "nt":
            if self.job is not None:
                try:
                    if not self.kernel.TerminateJobObject(self.job, 1):
                        raise OSError("Could not stop the owned Windows command group")
                finally:
                    self.kernel.CloseHandle(self.job)
                    self.job = None
        elif self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process is not None:
            self.process.wait()
