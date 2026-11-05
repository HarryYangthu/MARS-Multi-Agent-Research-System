"""Structured outputs exchanged between stages; every model answer is validated against one of these."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Module = str  # the part of the system a change touches, in the project's own terms (e.g. model, loss, training)


class ResearchQuestion(BaseModel):
    id: str
    question: str
    why: str
    paper_queries: list[str] = Field(min_length=1, max_length=3, description="English academic search queries")
    web_queries: list[str] = Field(default_factory=list, max_length=2, description="blog/tutorial search queries")


class Claim(BaseModel):
    claim: str
    kb_ids: list[str] = Field(description="knowledge chunk ids (K....) supporting the claim")


class Direction(BaseModel):
    id: str
    name: str
    hypothesis_seed: str
    why_promising: str
    risks: str


class ProblemAnalysis(BaseModel):
    baseline_understanding: str = Field(description="how the baseline works and where its cost and limitations lie")
    bottlenecks: list[str]
    history_lessons: list[Claim] = Field(min_length=2)
    knowledge_gaps: list[str]
    directions: list[Direction] = Field(min_length=3, max_length=6)
    research_questions: list[ResearchQuestion] = Field(min_length=3, max_length=8)


class Selection(BaseModel):
    source_id: str
    question_id: str
    reason: str


class Screening(BaseModel):
    selected: list[Selection] = Field(max_length=14)


class EvidenceCard(BaseModel):
    source_id: str
    mechanism: str = Field(description="the technical mechanism the source describes")
    relevance_to_problem: str = Field(description="how it could apply to this project's problem and baseline")
    efficiency_notes: str = Field(description="cost / resource implications relevant to the contract constraints")
    limitations: str
    quote: str = Field(description="a verbatim sentence copied from the source text supporting the mechanism")


class ModuleChange(BaseModel):
    module: Module
    change: str


class EvidenceUse(BaseModel):
    id: str = Field(description="evidence card id (E..) or knowledge chunk id (K....)")
    how_used: str


class Diagnostic(BaseModel):
    name: str = Field(description="one of the diagnostics the project declares")
    args: dict[str, Any] = Field(default_factory=dict, description="arguments for that diagnostic, as the project describes them")
    question: str = Field(description="what the result decides")


class Proposal(BaseModel):
    id: str
    title: str
    direction_id: str
    one_sentence: str
    mechanism_hypothesis: str
    changes: list[ModuleChange] = Field(min_length=1)
    removed_or_shrunk: str = Field(description="which baseline parts change: for a parameter goal what is removed or shrunk "
                                   "and where parameters are saved; for a performance goal which baseline limitation is removed")
    added_capability: str = Field(description="what the new part expresses: compensating what was removed, or what the baseline lacks")
    resource_estimate: str = Field(description="estimated cost against the contract's check gates (e.g. size, latency), with the arithmetic")
    constraints_argument: str = Field(description="why every gate and hard constraint of the contract is met")
    evidence: list[EvidenceUse] = Field(min_length=2)
    history_relation: str = Field(description="how it differs from historical attempts and rejected ideas in the project knowledge")
    expected_effect: str
    falsification: str = Field(description="which measured outcome would refute the mechanism hypothesis")
    implementation_spec: str = Field(description="concrete spec for the editable files: structure, shapes, initialization")
    training_overrides: dict[str, Any] = Field(default_factory=dict, description="values for the project's editable_config_keys")
    diagnostics: list[Diagnostic] = Field(default_factory=list, max_length=2,
                                          description="project diagnostics the system runs before prequick; results land in record evidence")


class Issue(BaseModel):
    severity: Literal["fatal", "major", "minor"]
    criterion: str
    issue: str
    required_fix: str


class Critique(BaseModel):
    proposal_id: str
    strengths: list[str]
    issues: list[Issue]
    verdict: Literal["accept", "revise", "reject"]


class Rebuttal(BaseModel):
    issue: str
    response: str


class Revision(BaseModel):
    rebuttals: list[Rebuttal]
    proposal: Proposal


class Score(BaseModel):
    proposal_id: str
    innovation: int = Field(ge=1, le=10)
    evidence: int = Field(ge=1, le=10)
    constraints_feasible: int = Field(ge=1, le=10)
    mechanism_clarity: int = Field(ge=1, le=10)
    history_distinct: int = Field(ge=1, le=10)
    rationale: str


class Judgement(BaseModel):
    scores: list[Score]


class NextChange(BaseModel):
    summary: str
    modules: list[Module]
    implementation_spec: str
    expected_observation: str
    discriminates_between: str = Field(description="which competing explanations this change tells apart")
    diagnostics: list[Diagnostic] = Field(default_factory=list, max_length=2,
                                          description="project diagnostics the system runs before prequick; results land in record evidence")


class Decision(BaseModel):
    verdict: Literal["continue", "promote", "refuted", "stop"]
    mechanism_assessment: str
    evidence_for: list[str]
    evidence_against: list[str]
    alternative_explanations: list[str]
    next_change: NextChange | None = None
    rationale: str
    operator_notes: list[dict[str, Any]] = Field(default_factory=list, description="per operator note: {note, adopted, reason}")
