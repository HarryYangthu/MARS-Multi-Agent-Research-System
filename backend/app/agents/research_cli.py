"""CLI specializations of the existing native agent loop, with executable delivery."""
from __future__ import annotations

import ast
from dataclasses import replace
import json
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import Message
from app.harness.research_trial import candidate_factory_config
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
    if len(factories) == 1 and len(factories[0].args.args) == 1:
        parameter = factories[0].args.args[0].arg
        for node in ast.walk(factories[0]):
            key: ast.expr | None = None
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == parameter:
                key = node.slice
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == parameter
                    and node.func.attr == "get" and node.args):
                key = node.args[0]
            if isinstance(key, ast.Constant) and key.value not in {"channels", "baseline", "context"}:
                errors.append("Factory config has only channels, baseline and context; architecture choices belong in the candidate")
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
        "when present in host feedback; never predict fabricated achieved scores. Explain in Chinese. "
        "The exact factory_config is supplied by the same helper used by the worker. No rank/model/seed "
        "keys are added at runtime: encode architecture choices in the candidate module. Host hashes the "
        "candidate separately from the common training protocol and counts actual parameters itself. "
        "The supplied frozen source text is real source-reading evidence; tools are intentionally disabled "
        "for this stage. Keep the body concise: implementation, interface assumptions and pending checks; "
        "do not repeat the entire research essay. Input finite complex64 is a host precondition, not a "
        "requirement to support arbitrary invalid inputs. Short-budget scores do not identify physical rank."
    )

    def __init__(self, model: str, loop: dict[str, Any], *, generation: dict[str, Any] | None = None) -> None:
        original = get_agent_config(self.name)
        super().__init__(agent_config=replace(original, model_name=model, tools=(),
            raw={**original.raw, "loop": loop}, debate_enabled=False, **(generation or {})))

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        return await self._draft_via_llm(request, context)

    async def build_context(self, request: RunRequest) -> ContextPack:
        context = await super().build_context(request)
        if "frozen_protocol" in request.upstream_artifacts:
            frozen = json.loads(request.upstream_artifacts["frozen_protocol"])
            context.upstream["factory_config"] = json.dumps(candidate_factory_config(frozen), ensure_ascii=False)
        return context

    def reflection_rubric(self) -> str:
        return (
            "Review the exact runnable candidate under supplied factory_config and the frozen protocol. "
            "Reject wrong tensor semantics, missing keys, invalid gradients, incorrect counts, changed "
            "baseline/evaluator, unsupported measured claims or architecture inconsistent with the approved "
            "plan. Cite a concrete execution failure or false assertion for each blocker. Do not require "
            "unprovided config keys, unrelated optimizer paths, invalid-input support, unbudgeted studies "
            "or a proof of the true system rank. Valid input preconditions need not be runtime assertions. "
            "An omitted multiplicative 1, a documented unused fallback or a future limitation is not a "
            "material blocker when the fixed experiment is executable. The source text in upstream is "
            "actual frozen code evidence. Parameters/checkpoints are independently checked by the host "
            "after this review; do not demand candidate measurements before allowing execution."
        )

    def review_messages(self, request: RunRequest, context: ContextPack) -> list[Message]:
        # Do not replay the author's 'submit a document' system instructions to
        # a tool-free reviewer that must return only an accept/issues decision.
        messages = [Message(role="system", content="You are an independent reviewer of an existing candidate. "
                    "Do not author or submit a document and do not call tools. Use the review JSON instruction "
                    "supplied by the host. Read the exact task, immutable inputs and submission contract."),
                    Message(role="system", content=context.project),
                    Message(role="user", content=context.task)]
        messages.extend(Message(role="user", content=f"[untrusted upstream:{name}]\n{value}")
                        for name, value in context.upstream.items())
        messages.append(Message(role="system", content="Host-enforced submission contract for this exact run:\n"
                        + json.dumps(self.submission_schema(request), ensure_ascii=False)))
        return messages

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

    def reflection_rubric(self) -> str:
        return (
            "Check the report against actual host measurements, frozen budget and available evidence. "
            "Reject invented results, incorrect numerical comparisons, test leakage, unsupported causal "
            "claims and claims of convergence/generalization from a short single-seed run. Require explicit "
            "limitations and separate completed work from future studies. Do not require new experiments "
            "to accept a correct report of limited or negative results. Reused research must be labelled."
        )


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
        "promise. Include a concise Chinese plan and exact protocol_ack metadata from frozen_protocol. "
        "estimated_runs counts training runs only: one baseline plus goal.rounds candidates. ablations lists "
        "only those scheduled candidates; put all unscheduled designs under future_ablations. Final evaluation "
        "uses selected checkpoints without extra optimization. Do not expand a one-candidate budget into a "
        "rank sweep. build_model receives only config.channels, config.baseline and config.context; use the "
        "provided baseline.model settings, with no external protocol file access or speculative fallback keys. "
        "A short-budget comparison tests achieved quality for this implementation and budget, not the true "
        "physical rank; failure causes can remain unresolved without inventing extra experiments."
    )

    def submission_schema(self, request: RunRequest) -> dict[str, Any]:
        schema = BaseAgent.submission_schema(self, request)
        assert schema is not None
        frozen = json.loads(request.upstream_artifacts["frozen_protocol"])
        rounds = int(json.loads(request.upstream_artifacts["goal"])["rounds"])
        schema["required"] += ["protocol_ack", "hypothesis", "scheduled_trials"]
        schema["properties"]["protocol_ack"] = {"const": self.protocol_ack(frozen)}
        schema["properties"]["hypothesis"] = {"type": "string", "minLength": 20}
        schema["properties"]["estimated_runs"] = {"const": 1 + rounds}
        schema["properties"]["scheduled_trials"] = {
            "const": ["baseline", *[f"round_{i:02d}" for i in range(1, rounds + 1)]]}
        schema["properties"]["ablations"].update(minItems=rounds, maxItems=rounds)
        schema["properties"]["future_ablations"] = {"type": "array", "items": {"type": "string"}}
        return schema

    @staticmethod
    def protocol_ack(frozen: dict[str, Any]) -> dict[str, Any]:
        # The complete host record avoids an author/reviewer disagreement over
        # whether a deliberately selected acknowledgement subset is complete.
        return dict(frozen)

    def reflection_rubric(self) -> str:
        return (
            "Check that the plan can be executed within the immutable host goal and supplied native "
            "submission schema. scheduled_trials and estimated_runs are exact host limits; never require "
            "additional runs or fields that contradict the schema. Check data identity, matched training, "
            "implementation interface, parameter arithmetic and honest limitations. Future ablations must "
            "be labelled unscheduled. This is a bounded engineering/quality comparison: it need not identify "
            "the true physical rank or distinguish optimization failure from model capacity. Require that "
            "those mechanisms remain inconclusive, not invented thresholds or unavailable diagnostics. "
            "Reject actual unsupported claims or infeasible scheduled work; do not reject an explicitly "
            "acknowledged single-seed/short-budget limitation merely because a larger study would be better."
        )

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
        "protocol and untouched test data: this run cannot return to coding after final test. "
        "If research_provenance identifies a source_run_root, the approved research and its real API calls "
        "were inherited from that prior run; distinguish them from newly executed stages."
    )

    def submission_schema(self, request: RunRequest) -> dict[str, Any]:
        schema = BaseAgent.submission_schema(self, request)
        assert schema is not None
        return schema
