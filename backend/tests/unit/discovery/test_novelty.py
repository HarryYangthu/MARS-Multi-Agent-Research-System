from __future__ import annotations

import pytest

from app.harness.discovery.novelty import (
    DuplicateKind,
    FingerprintBundle,
    NoveltyIndex,
    behavior_fingerprint,
    cosine_similarity,
    exact_fingerprint,
    normalized_ast_fingerprint,
    semantic_fingerprint,
)


def test_ast_and_behavior_fingerprints_ignore_incidental_variation() -> None:
    assert normalized_ast_fingerprint("value=1\n") == normalized_ast_fingerprint(
        "value = 1  # formatting only\n"
    )
    assert behavior_fingerprint({"curve": [1.00000001]}, precision=6) == behavior_fingerprint(
        {"curve": [1.00000002]}, precision=6
    )


def test_novelty_index_checks_layers_in_trust_order() -> None:
    index = NoveltyIndex(semantic_threshold=0.99)
    base = FingerprintBundle(
        exact=exact_fingerprint({"a": 1}),
        structural=normalized_ast_fingerprint("result = input_value + 1"),
        behavioral=behavior_fingerprint([0.1, 0.2]),
        semantic=semantic_fingerprint((1.0, 0.0)),
    )
    assert index.register("candidate-a", base, embedding=(1.0, 0.0)).is_novel

    exact = index.assess(base, embedding=(0.0, 1.0))
    assert exact.duplicate_kind == DuplicateKind.EXACT
    assert exact.matching_candidate_id == "candidate-a"

    structural = index.assess(
        FingerprintBundle(
            exact=exact_fingerprint({"a": 2}),
            structural=base.structural,
            behavioral=behavior_fingerprint([0.3]),
        )
    )
    assert structural.duplicate_kind == DuplicateKind.STRUCTURAL

    behavioral = index.assess(
        FingerprintBundle(
            exact=exact_fingerprint({"a": 3}),
            structural=normalized_ast_fingerprint("result = input_value - 1"),
            behavioral=base.behavioral,
        )
    )
    assert behavioral.duplicate_kind == DuplicateKind.BEHAVIORAL

    semantic = index.assess(
        FingerprintBundle(
            exact=exact_fingerprint({"a": 4}),
            semantic=base.semantic,
        ),
        embedding=(0.9999, 0.0001),
    )
    assert semantic.duplicate_kind == DuplicateKind.SEMANTIC
    assert semantic.semantic_similarity == 1.0


def test_semantic_helpers_validate_dimensions_and_zero_vectors() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="equal dimensions"):
        cosine_similarity((1.0,), (1.0, 0.0))
    with pytest.raises(ValueError, match="norm"):
        semantic_fingerprint((0.0, 0.0))
