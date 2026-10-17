"""Pinned Windows read handles: reject reparse points and deny rename/delete.

Each path component stays open until its consumer finishes. Directory listing
uses a path because Windows scandir cannot accept a CRT file descriptor.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
        ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
        ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD), ('size_low', wintypes.DWORD),
        ('links', wintypes.DWORD), ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


def _kernel() -> Any:
    if sys.platform != 'win32':
        raise RuntimeError('Windows handle reader requires Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


@contextmanager
def _pin(path: Path, *, directory: bool) -> Iterator[int]:
    if sys.platform != 'win32':
        raise RuntimeError('Windows handle reader requires Windows')
    api = _kernel()
    # OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.
    # FILE_SHARE_DELETE is deliberately absent; ancestors cannot be swapped.
    handle = api.CreateFileW(str(path), 0x80 if directory else 0x80000000, 3, None,
                             3, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _FileInfo()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError('只能读取普通代码文件或目录，不能通过重解析点读取')
        if not directory and info.links != 1:
            raise ValueError('只能读取普通代码文件，不能读取硬链接')
        yield int(handle)
    finally:
        api.CloseHandle(handle)


@contextmanager
def pinned_path(root: Path, name: str, *, directory: bool) -> Iterator[tuple[Path, int]]:
    if sys.platform != 'win32':
        raise RuntimeError('Windows handle reader requires Windows')
    relative = PurePosixPath(name)
    if not root.is_absolute() or relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
        raise ValueError('Invalid repository path')
    with ExitStack() as stack:
        current = Path(root.anchor)
        handle = stack.enter_context(_pin(current, directory=True))
        for part in root.parts[1:]:
            current = current / part
            handle = stack.enter_context(_pin(current, directory=True))
        for index, part in enumerate(relative.parts):
            current = current / part
            handle = stack.enter_context(_pin(current, directory=directory or index < len(relative.parts) - 1))
        if not relative.parts and not directory:
            raise ValueError('请选择代码文件')
        yield current, handle


@contextmanager
def file_descriptor(root: Path, name: str) -> Iterator[int]:
    if sys.platform != 'win32':
        raise RuntimeError('Windows handle reader requires Windows')
    import msvcrt

    # CRT takes ownership; duplicate the native handle so _pin remains its owner.
    with pinned_path(root, name, directory=False) as (_, handle):
        import _winapi

        process = _winapi.GetCurrentProcess()
        duplicate = _winapi.DuplicateHandle(process, handle, process, 0, False, _winapi.DUPLICATE_SAME_ACCESS)
        try:
            descriptor = msvcrt.open_osfhandle(duplicate, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            _winapi.CloseHandle(duplicate)
            raise
        try:
            yield descriptor
        finally:
            os.close(descriptor)
