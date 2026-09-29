"""Read-only browsing of the project's current bound repository (not a run snapshot)."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterator

import yaml

from app.harness.runtime.project_scope import forbidden_source_path
from app.harness.tools.project_repo import ProjectRepo, load_project_repo, resolve_allowed_path
from app.settings import repo_root
from app.storage.run_store import RunHandle


@dataclass(frozen=True)
class BrowsePolicy:
    directory_page_size: int
    file_page_lines: int
    max_file_bytes: int

    @classmethod
    def load(cls) -> BrowsePolicy:
        raw = yaml.safe_load((repo_root() / 'configs/code_review.yaml').read_text())['repository']
        return cls(**{key: int(raw[key]) for key in cls.__dataclass_fields__})


class CodeRepository:
    def __init__(self, repo: ProjectRepo, policy: BrowsePolicy) -> None:
        self.repo, self.policy = repo, policy
        if repo.root == repo.root.parent or not repo.root.is_dir() or repo.root.is_symlink():
            raise ValueError('请先配置有效的代码工程文件夹')
        if min(policy.directory_page_size, policy.file_page_lines, policy.max_file_bytes) <= 0:
            raise ValueError('代码浏览配置无效')
        info = repo.root.stat()
        self.token = hashlib.sha256(json.dumps([
            str(repo.root), info.st_dev, info.st_ino, repo.allowed_paths, repo.ignore_patterns,
        ]).encode()).hexdigest()

    def _name(self, path: str, *, directory: bool = False) -> str:
        name = PurePosixPath(path)
        if name.is_absolute() or '..' in name.parts or '\\' in path or '\x00' in path:
            raise ValueError('文件路径无效')
        if not name.parts:
            if directory:
                return ''
            raise ValueError('请选择代码文件')
        value = name.as_posix()
        if forbidden_source_path(value):
            raise ValueError('该路径不属于可浏览源码')
        ancestors = [PurePosixPath(*name.parts[:i]).as_posix() for i in range(1, len(name.parts) + 1)]
        for part in ancestors:
            if any(fnmatch.fnmatchcase(part, pattern) or fnmatch.fnmatchcase(part + '/', pattern)
                   for pattern in self.repo.ignore_patterns if pattern):
                raise ValueError('该路径已被项目配置排除')
        return value

    def _directory_allowed(self, name: str) -> bool:
        for rule in self.repo.allowed_paths:
            # Ancestors of permitted files must remain navigable, including glob prefixes.
            prefix = rule
            for wildcard in '*?[':
                prefix = prefix.split(wildcard, 1)[0]
            prefix = prefix.rstrip('/')
            if not prefix or name == prefix or prefix.startswith(name + '/') or name.startswith(prefix + '/'):
                return True
        return False

    @contextmanager
    def _open(self, name: str, *, directory: bool) -> Iterator[int]:
        """Walk with directory descriptors: even concurrent symlink swaps cannot escape."""
        fd = os.open(self.repo.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = PurePosixPath(name).parts
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if directory or index < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('只能读取普通代码文件')
            yield fd
        finally:
            os.close(fd)

    def _identity(self, token: str) -> None:
        if token and token != self.token:
            raise ValueError('代码工程关联已变更，请重新打开代码窗口')

    def directory(self, path: str = '', *, offset: int = 0, token: str = '') -> dict[str, Any]:
        self._identity(token)
        name = self._name(path, directory=True)
        if offset < 0 or (name and not self._directory_allowed(name)):
            raise ValueError('目录范围无效')
        entries: list[dict[str, Any]] = []
        with self._open(name, directory=True) as fd, os.scandir(fd) as children:
            for child in children:
                relative = f'{name}/{child.name}' if name else child.name
                try:
                    self._name(relative)
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode) and self._directory_allowed(relative):
                        kind = 'directory'
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        resolve_allowed_path(self.repo, relative)
                        kind = 'file'
                    else:
                        continue
                except (ValueError, OSError):
                    continue
                entries.append({'path': relative, 'name': child.name, 'kind': kind, 'size_bytes': info.st_size})
        entries.sort(key=lambda row: (row['kind'] != 'directory', row['name'].casefold(), row['name']))
        end = offset + self.policy.directory_page_size
        return {'path': name, 'root_name': self.repo.root.name, 'root_path': str(self.repo.root),
                'read_only': self.repo.read_only, 'source': 'project_current', 'repository_token': self.token,
                'entries': entries[offset:end], 'next_offset': end if end < len(entries) else None,
                'total': len(entries)}

    def file(self, path: str, *, start: int = 0, version: str = '', token: str = '') -> dict[str, Any]:
        self._identity(token)
        name = self._name(path)
        resolve_allowed_path(self.repo, name)
        if start < 0:
            raise ValueError('行号无效')
        with self._open(name, directory=False) as fd:
            if os.fstat(fd).st_size > self.policy.max_file_bytes:
                raise ValueError('文件超过在线阅读大小限制，请在本地代码工程中打开')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(self.policy.max_file_bytes + 1)
        if len(raw) > self.policy.max_file_bytes:
            raise ValueError('文件超过在线阅读大小限制，请在本地代码工程中打开')
        digest = hashlib.sha256(raw).hexdigest()
        if version and version != digest:
            raise ValueError('文件内容已更新，请重新读取，避免混合不同版本')
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('该文件不是 UTF-8 文本，请在本地打开') from exc
        if '\x00' in content:
            raise ValueError('二进制文件不支持代码预览')
        lines = content.splitlines(keepends=True)
        end = start + self.policy.file_page_lines
        return {'path': name, 'repository_token': self.token, 'source': 'project_current',
                'version': digest, 'size_bytes': len(raw), 'total_lines': len(lines), 'start': start,
                'lines': lines[start:end], 'next_start': end if end < len(lines) else None}


def run_code_repository(run: RunHandle, *, project: str) -> CodeRepository:
    if run.project != project:
        raise ValueError('代码工程所属项目不匹配')
    return CodeRepository(load_project_repo(project), BrowsePolicy.load())
