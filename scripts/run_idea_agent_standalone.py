#!/usr/bin/env python3
"""Run Idea Agent alone and show its research artifacts.

This is intentionally narrower than the full pipeline demo: it creates a run,
executes only IdeaAgent, persists proposal.v1, and prints the research pack so
the user can inspect what the agent gathered before drafting.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

DEFAULT_QUESTION = (
    "如何在 FDD Massive MIMO 的 beam/layer 切换场景下，用 PIMC routing "
    "降低计算量并保持 RES <= -26 dB?"
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run only Idea Agent and print its research artifacts."
    )
    parser.add_argument("--project", default="pimc")
    parser.add_argument("--task", default="idea_standalone")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--mock-mode",
        choices=("auto", "always", "never"),
        default="always",
        help="Default is always so the standalone smoke test needs no LLM key.",
    )
    parser.add_argument(
        "--network-research",
        action="store_true",
        help=(
            "Mark network research as requested. V2 records this in the "
            "research pack; it does not require a web fetcher."
        ),
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="Optional alternate runs root, useful for tests.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=1200,
        help="Characters to print from markdown research artifacts. Use 0 for paths only.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    os.environ["MARS_MOCK_MODE"] = args.mock_mode
    if args.mock_mode == "always":
        for key in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "QWEN_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
            "CUSTOM_ENDPOINT_URL",
            "CUSTOM_ENDPOINT_API_KEY",
        ):
            os.environ.pop(key, None)
        os.environ["LOCAL_VLLM_BASE_URL"] = ""

    from app.agents.base import RunRequest
    from app.agents.idea.agent import IdeaAgent
    from app.harness.schema.validator import validate_document
    from app.storage.artifact_store import ArtifactStore
    from app.storage.run_store import RunStore

    runs_root = args.runs_root.expanduser().resolve() if args.runs_root else None
    store = RunStore(runs_root=runs_root)
    run = store.create(
        task=args.task,
        project=args.project,
        entrypoint="idea",
        user_request=args.question,
    )

    debate_path = run.subdir("idea") / "debate_transcript.v1.md"
    research_dir = run.subdir("idea") / "research"
    request = RunRequest(
        project=args.project,
        user_request=args.question,
        extra={
            "run_id": run.run_id,
            "run_root": str(run.root),
            "node_key": "idea",
            "agent_dir": str(run.subdir("idea")),
            "idea_research_dir": str(research_dir),
            "debate_progress_path": str(debate_path),
            "enable_network_research": args.network_research,
        },
    )

    agent = IdeaAgent()
    context = await agent.build_context(request)
    artifact = await agent.run_loop(request, context)
    validation = validate_document(artifact.text, expected_schema="proposal.v1")
    if not validation.valid:
        print("[ERR] proposal.v1 failed validation", file=sys.stderr)
        for error in validation.errors:
            print(f"      {error.path}: {error.message}", file=sys.stderr)
        return 1

    ref = ArtifactStore(run).write(
        text=artifact.text,
        agent_dir="idea",
        stem="idea_proposal",
        expected_schema="proposal.v1",
        version="v1",
    )

    _print_result(
        run_root=run.root,
        proposal_path=ref.path,
        research_dir=research_dir,
        debate_path=debate_path,
        metadata=artifact.metadata,
        preview_chars=max(0, int(args.preview_chars)),
    )
    return 0


def _print_result(
    *,
    run_root: Path,
    proposal_path: Path,
    research_dir: Path,
    debate_path: Path,
    metadata: dict[str, Any],
    preview_chars: int,
) -> None:
    print(f"[run] {run_root.name}")
    print(f"[run_root] {run_root}")
    print(f"[proposal] {proposal_path}")
    print(f"[research_dir] {research_dir}")
    if debate_path.exists():
        print(f"[debate] {debate_path}")

    print("\n[research artifacts]")
    for name in (
        "research_plan.v1.md",
        "research_notes.v1.md",
        "research_summary.v1.md",
        "evidence_index.v1.json",
    ):
        path = research_dir / name
        status = "OK" if path.exists() else "MISSING"
        print(f"- [{status}] {path}")

    print("\n[proposal frontmatter summary]")
    for key in (
        "research_question",
        "hypothesis",
        "novelty",
        "quality_warnings",
    ):
        value = metadata.get(key)
        if value:
            print(f"- {key}: {_compact(value)}")

    if preview_chars <= 0:
        return

    for name in ("research_summary.v1.md", "evidence_index.v1.json"):
        path = research_dir / name
        if not path.exists():
            continue
        print(f"\n--- {name} preview ---")
        text = path.read_text(encoding="utf-8")
        if name.endswith(".json"):
            try:
                parsed = json.loads(text)
                text = json.dumps(parsed, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
        print(text[:preview_chars].rstrip())
        if len(text) > preview_chars:
            print("...")


def _compact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return " ".join(text.split())[:500]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
