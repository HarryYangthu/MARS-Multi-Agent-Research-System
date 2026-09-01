"""Sanitized environments for trusted execution subprocesses.

Agent/provider credentials belong to the MARS control plane.  Project Pack
adapters and external research programs receive only ordinary process state
plus composition-owned overrides; ambient secrets and interpreter injection
variables are removed before launch.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


_SECRET_NAME_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "auth_token",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "session_token",
    "ssh_key",
    "token",
)
_BLOCKED_AMBIENT_NAMES = frozenset(
    {
        "all_proxy",
        "gpg_agent_info",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "pythonhome",
        "pythoninspect",
        "pythonpath",
        "pythonstartup",
        "ssh_auth_sock",
    }
)


def sanitized_subprocess_environment(
    *,
    inherited: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return child state without ambient credentials or injection hooks.

    ``overrides`` is the composition-owned adapter environment.  It is applied
    after ambient filtering so a Project Pack may deliberately supply its own
    ``PYTHONPATH`` while secret-looking keys remain forbidden in both sources.
    """

    source = os.environ if inherited is None else inherited
    result = {
        name: value
        for name, value in source.items()
        if not _blocked_ambient_name(name) and not _secret_name(name)
    }
    for name, value in (overrides or {}).items():
        if _secret_name(name):
            continue
        result[str(name)] = str(value)
    return result


def _blocked_ambient_name(name: str) -> bool:
    normalized = name.strip().casefold()
    return (
        normalized in _BLOCKED_AMBIENT_NAMES
        or normalized.startswith("dyld_")
        or normalized == "ld_preload"
    )


def _secret_name(name: str) -> bool:
    normalized = name.strip().casefold()
    return normalized.startswith("mars_remote_ssh_") or any(
        marker in normalized for marker in _SECRET_NAME_MARKERS
    )
