import type {
  DebateView,
  DiscoveryConfigView,
  DiscoveryHypothesis,
  DiscoveryMatch,
  DiscoveryReflection,
  IdeaDiscoverySnapshot,
  MetaReviewView,
  ProximityEdgeView,
  ProximityGraphView,
} from "./types";

export function normalizeIdeaDiscoveryPayload(value: unknown): IdeaDiscoverySnapshot {
  const source = requireRecord(value, "Idea discovery response");
  const hypotheses = recordArray(source.hypotheses).map(normalizeHypothesis);
  const reflections = recordArray(source.reflections).map(normalizeReflection);
  const matches = recordArray(source.matches ?? source.pairwise_matches).map(normalizeMatch);
  const metaReviews = normalizeMetaReviews(source);
  const roundIndex = numberValue(
    source.round_index,
    Math.max(0, ...hypotheses.map((item) => item.round_index)),
  );
  return {
    run_id: stringValue(source.run_id),
    project: stringValue(source.project),
    status: stringValue(source.status, "unknown"),
    round_index: roundIndex,
    backend_mode: stringValue(source.backend_mode),
    config: normalizeConfig(source.config),
    hypotheses,
    reflections,
    matches,
    proximity_graphs: recordArray(source.proximity_graphs).map(normalizeProximityGraph),
    meta_reviews: metaReviews,
    debate: normalizeDebate(source.debate ?? source.debate_summary),
    finalist_ids: stringArray(source.finalist_ids ?? source.top_hypothesis_ids),
    selected_id: stringValue(source.selected_id ?? source.selected_hypothesis_id),
    proposal_ref: stringValue(source.proposal_ref),
  };
}

export function hypothesisById(
  snapshot: IdeaDiscoverySnapshot,
): Map<string, DiscoveryHypothesis> {
  return new Map(snapshot.hypotheses.map((item) => [item.hypothesis_id, item]));
}

function normalizeHypothesis(source: Record<string, unknown>): DiscoveryHypothesis {
  return {
    hypothesis_id: stringValue(source.hypothesis_id),
    statement: stringValue(source.statement),
    mechanism: stringValue(source.mechanism),
    cluster_id: stringValue(source.cluster_id),
    elo: numberValue(source.elo, 1000),
    blocked: Boolean(source.blocked),
    parent_ids: stringArray(source.parent_ids),
    evidence_refs: stringArray(source.evidence_refs),
    testable_predictions: stringArray(source.testable_predictions),
    constraints: stringArray(source.constraints),
    operator: stringValue(source.operator, "generate"),
    round_index: numberValue(source.round_index, 0),
    uncertainty: stringValue(source.uncertainty),
  };
}

function normalizeReflection(source: Record<string, unknown>): DiscoveryReflection {
  return {
    reflection_id: stringValue(source.reflection_id),
    hypothesis_id: stringValue(source.hypothesis_id),
    correctness: stringValue(source.correctness),
    novelty: stringValue(source.novelty),
    falsifiability: stringValue(source.falsifiability),
    assumptions: stringArray(source.assumptions),
    failure_modes: stringArray(source.failure_modes),
    blockers: stringArray(source.blockers),
    evidence_refs: stringArray(source.evidence_refs),
  };
}

function normalizeMatch(source: Record<string, unknown>): DiscoveryMatch {
  const rawOutcome = stringValue(source.outcome, "draw");
  const outcome = rawOutcome === "left" || rawOutcome === "right" ? rawOutcome : "draw";
  return {
    match_id: stringValue(source.match_id),
    left_id: stringValue(source.left_id),
    right_id: stringValue(source.right_id),
    outcome,
    reason: stringValue(source.reason),
    left_rating_before: numberValue(source.left_rating_before, 1000),
    right_rating_before: numberValue(source.right_rating_before, 1000),
    left_rating_after: numberValue(source.left_rating_after, 1000),
    right_rating_after: numberValue(source.right_rating_after, 1000),
    round_index: numberValue(source.round_index, 0),
    evidence_refs: stringArray(source.evidence_refs),
  };
}

function normalizeProximityGraph(source: Record<string, unknown>): ProximityGraphView {
  const rawClusters = isRecord(source.clusters) ? source.clusters : {};
  const clusters = Object.fromEntries(
    Object.entries(rawClusters).map(([name, ids]) => [name, stringArray(ids)]),
  );
  return {
    round_index: numberValue(source.round_index, 0),
    clusters,
    edges: recordArray(source.edges).map(normalizeProximityEdge),
  };
}

function normalizeProximityEdge(source: Record<string, unknown>): ProximityEdgeView {
  return {
    left_id: stringValue(source.left_id),
    right_id: stringValue(source.right_id),
    similarity: numberValue(source.similarity, 0),
    exact_duplicate: Boolean(source.exact_duplicate),
  };
}

function normalizeMetaReviews(source: Record<string, unknown>): MetaReviewView[] {
  const records = recordArray(source.meta_reviews);
  if (records.length) return records.map(normalizeMetaReview);
  return isRecord(source.meta_review) ? [normalizeMetaReview(source.meta_review)] : [];
}

function normalizeMetaReview(source: Record<string, unknown>): MetaReviewView {
  return {
    meta_review_id: stringValue(source.meta_review_id),
    round_index: numberValue(source.round_index, 0),
    recurring_errors: stringArray(source.recurring_errors),
    successful_patterns: stringArray(source.successful_patterns),
    evidence_gaps: stringArray(source.evidence_gaps),
    unexplored_regions: stringArray(source.unexplored_regions),
    next_round_guidance: stringArray(source.next_round_guidance),
  };
}

function normalizeDebate(value: unknown): DebateView | null {
  if (!isRecord(value)) return null;
  return {
    status: stringValue(value.status, "recorded"),
    summary: stringValue(value.summary ?? value.consensus),
    disagreements: stringArray(value.disagreements),
    evidence_gaps: stringArray(value.evidence_gaps),
  };
}

function normalizeConfig(value: unknown): DiscoveryConfigView | null {
  if (!isRecord(value)) return null;
  return {
    budget_profile: stringValue(value.budget_profile, "balanced"),
    initial_hypotheses: numberValue(value.initial_hypotheses, 0),
    evolution_rounds: numberValue(value.evolution_rounds, 0),
    max_pairwise_matches: numberValue(value.max_pairwise_matches, 0),
    top_k: numberValue(value.top_k, 0),
  };
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  return value;
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => isRecord(item))
    : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
