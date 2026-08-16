"""Run-local persistence for Idea deep discovery.

The store intentionally uses replace-style JSON snapshots instead of JSONL
appends.  Replaying a completed stage therefore cannot duplicate hypotheses,
matches, or budget-like counters.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agents.idea.discovery.models import (
    DeepDiscoveryState,
    HypothesisPoolView,
    HypothesisSelection,
)


class DiscoveryInputMismatchError(RuntimeError):
    """Raised when a run-local checkpoint belongs to different inputs."""


class RunLocalDiscoveryStore:
    def __init__(self, root: Path | None) -> None:
        self.root = root

    @property
    def enabled(self) -> bool:
        return self.root is not None

    def load_state(self, *, input_hash: str) -> DeepDiscoveryState | None:
        if self.root is None:
            return None
        path = self.root / "state.v1.json"
        if not path.exists():
            return None
        state = DeepDiscoveryState.model_validate_json(path.read_text(encoding="utf-8"))
        if state.input_hash != input_hash:
            raise DiscoveryInputMismatchError(
                "existing Idea discovery checkpoint was created from different inputs"
            )
        return state

    def save_state(self, state: DeepDiscoveryState) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_model("state.v1.json", state)
        self._write_json(
            "checkpoint.v1.json",
            {
                "schema_id": "idea_discovery_checkpoint.v1",
                "run_id": state.run_id,
                "input_hash": state.input_hash,
                "status": state.status,
                "completed_stages": list(state.completed_stages),
                "hypothesis_count": len(state.hypotheses),
                "reflection_count": len(state.reflections),
                "match_count": len(state.matches),
                "meta_review_count": len(state.meta_reviews),
            },
        )
        self._write_records(
            "hypotheses.v1.json", [item.model_dump(mode="json") for item in state.hypotheses]
        )
        self._write_records(
            "reflections.v1.json", [item.model_dump(mode="json") for item in state.reflections]
        )
        self._write_records(
            "pairwise_matches.v1.json",
            [item.model_dump(mode="json") for item in state.matches],
        )
        self._write_records(
            "proximity_graphs.v1.json",
            [item.model_dump(mode="json") for item in state.proximity_graphs],
        )
        self._write_records(
            "meta_reviews.v1.json",
            [item.model_dump(mode="json") for item in state.meta_reviews],
        )
        self._write_model("hypothesis_pool.v1.json", pool_view(state))

    def load_selection(self) -> HypothesisSelection | None:
        if self.root is None:
            return None
        path = self.root / "selection.v1.json"
        if not path.exists():
            return None
        return HypothesisSelection.model_validate_json(path.read_text(encoding="utf-8"))

    def save_selection(self, selection: HypothesisSelection) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.load_selection()
        if existing is not None and existing != selection:
            raise RuntimeError("a different hypothesis has already been selected")
        self._write_model("selection.v1.json", selection)

    def _write_model(self, name: str, value: Any) -> None:
        payload = value.model_dump(mode="json")
        self._write_json(name, payload)

    def _write_records(self, name: str, records: list[dict[str, Any]]) -> None:
        self._write_json(
            name,
            {
                "schema_id": name.removesuffix(".json"),
                "count": len(records),
                "items": records,
            },
        )

    def _write_json(self, name: str, payload: object) -> None:
        assert self.root is not None
        path = self.root / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def pool_view(state: DeepDiscoveryState) -> HypothesisPoolView:
    legal = [item for item in state.hypotheses if not item.blocked]
    return HypothesisPoolView(
        run_id=state.run_id,
        project=state.project,
        status=state.status,
        config=state.config,
        hypothesis_count=len(state.hypotheses),
        legal_count=len(legal),
        match_count=len(state.matches),
        top_hypothesis_ids=state.top_hypothesis_ids,
        selected_hypothesis_id=state.selected_hypothesis_id,
        record_refs={
            "hypotheses": "idea/discovery/hypotheses.v1.json",
            "reflections": "idea/discovery/reflections.v1.json",
            "matches": "idea/discovery/pairwise_matches.v1.json",
            "proximity": "idea/discovery/proximity_graphs.v1.json",
            "meta_reviews": "idea/discovery/meta_reviews.v1.json",
            "checkpoint": "idea/discovery/checkpoint.v1.json",
        },
        warnings=state.warnings,
    )
