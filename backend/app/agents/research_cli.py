"""CLI specializations of the existing native agent loop, with executable delivery."""
from __future__ import annotations

import ast
from dataclasses import replace
import json
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.llm.model_registry import get_agent_config
from app.harness.schema.frontmatter_parser import parse


def candidate_errors(source: str) -> list[str]:
    """Catch accidental I/O/evaluator edits. This lint is not a Python security sandbox."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [str(exc)]
    errors: list[str] = []
    factories = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_model"]
    if len(factories) != 1 or len(factories[0].args.args) != 1 or factories[0].args.vararg or factories[0].args.kwarg:
        errors.append("Define exactly one synchronous build_model(config) factory")
    allowed = ("__future__", "torch", "math", "copy", "typing", "collections", "dataclasses", "libs.model", "libs.model_static_compact")
    for node in ast.walk(tree):
        imports: list[str] = []
        if isinstance(node, ast.Import):
            imports = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [node.module or ""]
            if node.level:
                errors.append("Relative imports are not supported in standalone candidate files")
        if any(not any(m == a or m.startswith(a + ".") for a in allowed) for m in imports):
            errors.append("Imports must be tensor/model utilities only; no filesystem, network or evaluator imports")
        if isinstance(node, ast.Name) and node.id in {"open", "exec", "eval", "compile", "__import__", "globals", "locals"}:
            errors.append("Dynamic code execution and external I/O are forbidden in model definitions")
        if isinstance(node, ast.Attribute) and node.attr in {"load", "save", "set_default_dtype", "set_grad_enabled", "set_num_threads"}:
            errors.append("Model code must not mutate runtime settings or read/write checkpoints")
    return sorted(set(errors))


class ResearchCodingAgent(BaseAgent):
    name = "coding"
    output_schema = "code_spec.v1"
    native_structured_delivery = True
    agent_brief = (
        "Implement one falsifiable research hypothesis as a complete standalone Python model module. "
        "Use the supplied research, source files and actual validation feedback. The ONLY added file is "
        "libs/research_candidate.py with build_model(config). config contains channels=16, baseline and context. "
        "Return a torch.nn.Module with finite complex64 [time,16] input/output and trainable gradients. "
        "Reuse existing model components or implement new architecture; this is not a request to select a fixed grid. "
        "Count complex parameters as two real scalars. Never change training, metric, data splits or baseline source. "
        "No file/network I/O, dynamic imports, external weights or new dependencies. Include every code line in "
        "metadata.source_code, an explicit hypothesis and implementation rationale. Discuss measurements only "
        "when present in host feedback; never predict fabricated achieved scores. Explain in Chinese."
    )

    def __init__(self, model: str, loop: dict[str, Any]) -> None:
        original = get_agent_config(self.name)
        super().__init__(agent_config=replace(original, model_name=model, tools=(),
            raw={**original.raw, "loop": loop}, debate_enabled=False))

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        return await self._draft_via_llm(request, context)

    def submission_schema(self, request: RunRequest) -> dict[str, Any]:
        schema = super().submission_schema(request)
        assert schema is not None
        schema["required"] += ["source_code", "hypothesis"]
        schema["properties"]["source_code"] = {"type": "string", "minLength": 30, "maxLength": 60000}
        schema["properties"]["hypothesis"] = {"type": "string", "minLength": 20}
        schema["properties"]["baseline_compat"]["properties"]["preserved"] = {"const": True}
        schema["properties"]["files_changed"] = {"const": [{"path": "libs/research_candidate.py", "type": "added"}]}
        return schema

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if not errors:
            errors += candidate_errors(parse(text).metadata["source_code"])
        return errors


class ResearchAnalysisAgent(ResearchCodingAgent):
    name = "writing"
    output_schema = "report.v1"
    agent_brief = (
        "Analyze only the host's recorded validation results and execution errors against the frozen research "
        "goal. Explain supported/rejected hypotheses, numerical gaps, uncertainty and one concrete change for "
        "the next iteration. Final test data are unavailable during search. Do not invent experiments or change "
        "the target or protocol. Return decision=continue or stop and next_hypothesis in metadata; a concise "
        "Chinese Markdown analysis in body. The decision is advisory: the host runs the configured number "
        "of rounds before final selection. Host measurements alone determine success. A 50-step single-seed "
        "comparison is a workflow/limited-budget experiment, not proof of converged or general performance."
    )

    def submission_schema(self, request: RunRequest) -> dict[str, Any]:
        schema = BaseAgent.submission_schema(self, request)
        assert schema is not None
        schema["required"] += ["decision", "next_hypothesis"]
        schema["properties"]["decision"] = {"enum": ["continue", "stop"]}
        schema["properties"]["next_hypothesis"] = {"type": "string", "minLength": 10}
        return schema

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        return await BaseAgent.validate_candidate(self, request, text, observations)


class ResearchExperimentAgent(ResearchCodingAgent):
    """Turn actual data diagnostics and a reviewed hypothesis into a bounded plan."""

    name = "experiment"
    output_schema = "experiment_plan.v1"
    agent_brief = (
        "Design the actual matched experiment from the reviewed proposal, source and training-only data "
        "diagnostics. Explain the physical/statistical hypothesis, candidate intervention, expected failure "
        "modes, controlled factors, validation selection and independent final test. The host frozen_protocol "
        "and goal are immutable. All models start from scratch with the same seed and update budget. "
        "Separate experiments actually scheduled within remaining rounds from future ablations; never claim "
        "an unexecuted ablation is a result. Parameter reduction is an exploratory hypothesis, not a user "
        "promise. Include a concise Chinese plan and exact protocol_ack metadata from frozen_protocol."
    )

    def submission_schema(self, request: RunRequest) -> dict[str, Any]:
        schema = BaseAgent.submission_schema(self, request)
        assert schema is not None
        frozen = json.loads(request.upstream_artifacts["frozen_protocol"])
        schema["required"] += ["protocol_ack", "hypothesis"]
        schema["properties"]["protocol_ack"] = {"const": self.protocol_ack(frozen)}
        schema["properties"]["hypothesis"] = {"type": "string", "minLength": 20}
        return schema

    @staticmethod
    def protocol_ack(frozen: dict[str, Any]) -> dict[str, Any]:
        keys = ("seed", "max_steps", "split_guard", "train_fraction", "validation_fraction",
                "scale", "fs", "band", "metric", "selection", "initialization")
        return {key: frozen[key] for key in keys}

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await BaseAgent.validate_candidate(self, request, text, observations)
        if not errors:
            frozen = json.loads(request.upstream_artifacts["frozen_protocol"])
            if parse(text).metadata.get("protocol_ack") != self.protocol_ack(frozen):
                errors.append("Experiment plan must acknowledge the exact frozen protocol")
        return errors


class ResearchFinalReportAgent(ResearchAnalysisAgent):
    """Interpret final measurements only after selection, with no return to search."""

    agent_brief = (
        "Write the final Chinese simulation report using only supplied host measurements, approved research, "
        "experiment plan and code receipts. Final test is now available after candidate selection was frozen. "
        "Report baseline/candidate RES, per-channel and worst-channel values, parameter count, actual steps "
        "and time. Distinguish engineering completion, exploratory target attainment, convergence, novelty "
        "and generalization. Include actual failures, uncertainty, references and reproducibility limits. "
        "Do not fabricate prices, measurements or executed ablations. Any future redesign needs a new "
        "protocol and untouched test data: this run cannot return to coding after final test."
    )

    def submission_schema(self, request: RunRequest) -> dict[str, Any]:
        schema = BaseAgent.submission_schema(self, request)
        assert schema is not None
        return schema
