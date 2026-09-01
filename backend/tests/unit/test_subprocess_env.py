from __future__ import annotations

from app.execution.subprocess_env import sanitized_subprocess_environment


def test_explicit_pack_pythonpath_is_allowed_but_explicit_secrets_are_not() -> None:
    environment = sanitized_subprocess_environment(
        inherited={
            "PATH": "/usr/bin",
            "PYTHONPATH": "/ambient/injection",
            "GITHUB_TOKEN": "ambient-secret",
            "MARS_REMOTE_SSH_HOST": "private-gpu.example.test",
            "DYLD_INSERT_LIBRARIES": "/ambient/injection.dylib",
        },
        overrides={
            "PYTHONPATH": "/trusted/project-pack/src",
            "DEEPSEEK_API_KEY": "explicit-secret",
            "PACK_MODE": "cpu",
        },
    )

    assert environment == {
        "PATH": "/usr/bin",
        "PYTHONPATH": "/trusted/project-pack/src",
        "PACK_MODE": "cpu",
    }
