from __future__ import annotations

import pytest

from app.harness.discovery.code_candidate import (
    CodeCandidateSpec,
    TensorInterfaceSpec,
    inspect_code_candidate,
)


def _spec() -> CodeCandidateSpec:
    return CodeCandidateSpec(
        base_snapshot_id="snap_0123456789abcdef01234567",
        entrypoint="candidate/model.py",
        factory="build_model",
        patch_ref="artifact://coding/patch.diff",
        patch_sha256="sha256:" + "a" * 64,
        touched_paths=("candidate/model.py",),
        interface=TensorInterfaceSpec(
            input_rank=2,
            output_rank=2,
            input_dtype="complex64",
            output_dtype="complex64",
            preserve_shape=True,
        ),
    )


def test_code_candidate_accepts_safe_factory_and_allowed_path() -> None:
    report = inspect_code_candidate(
        _spec(),
        source="def build_model(config):\n    return config['model']\n",
        allowed_paths=("candidate/",),
        forbidden_paths=("baseline/",),
    )

    assert report.passed
    assert not report.blockers


def test_code_candidate_rejects_forbidden_path_and_invalid_factory() -> None:
    spec = _spec().model_copy(
        update={
            "entrypoint": "baseline/model.py",
            "touched_paths": ("baseline/model.py",),
        }
    )
    report = inspect_code_candidate(
        spec,
        source="async def build_model(config):\n    return config\n",
        allowed_paths=("baseline/",),
        forbidden_paths=("baseline/",),
    )

    assert not report.passed
    assert {check.check_id for check in report.blockers} == {
        "path:baseline/model.py",
        "factory",
    }


def test_code_candidate_reports_syntax_error_without_importing_source() -> None:
    report = inspect_code_candidate(
        _spec(),
        source="def build_model(:\n    pass\n",
        allowed_paths=("candidate/",),
    )

    assert not report.passed
    assert report.blockers[-1].check_id == "python_ast"


@pytest.mark.parametrize(
    "update",
    [
        {"entrypoint": "../model.py", "touched_paths": ("../model.py",)},
        {"factory": "build-model"},
        {"touched_paths": ("candidate/other.py",)},
    ],
)
def test_code_candidate_schema_rejects_unsafe_contract(update: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CodeCandidateSpec.model_validate(_spec().model_dump() | update)
