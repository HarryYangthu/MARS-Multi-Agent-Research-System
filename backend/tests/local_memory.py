"""Actual local files as a labelled memory corpus; no provider or tool replacement."""
from pathlib import Path
from app.harness.kb.provenance import record_artifact


def authored_note(base: Path, text: str, *, project: str = "pimc") -> dict[str, object]:
    import hashlib
    path = base / "authored_notes" / (hashlib.sha256(text.encode()).hexdigest() + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {**record_artifact(path=path, run_id="human-authored-corpus", project=project),
            "author_kind": "human", "scientific_validated": False}
