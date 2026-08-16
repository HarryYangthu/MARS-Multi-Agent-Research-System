import type {
  CandidateEvaluation,
  CandidateRecord,
  DiscoveryEvent,
  DiscoveryReplayView,
  FidelityLevel,
  MetricValue,
} from "./types";

export interface CandidateRow {
  candidate: CandidateRecord;
  evaluation: CandidateEvaluation | null;
  metrics: Record<string, MetricValue>;
  fidelity: FidelityLevel | null;
  preflight: "passed" | "blocked" | "pending";
  gate: "passed" | "blocked" | "pending";
  pareto: boolean;
}
export function latestEvaluation(
  replay: DiscoveryReplayView,
  candidateId: string,
): CandidateEvaluation | null {
  const found = replay.evaluations.filter((item) => item.candidate_id === candidateId);
  return found.at(-1) ?? null;
}

export function eventsForCandidate(
  replay: DiscoveryReplayView,
  candidateId: string,
): DiscoveryEvent[] {
  return replay.events.filter((event) => event.candidate_id === candidateId);
}

export function candidateRows(replay: DiscoveryReplayView): CandidateRow[] {
  const paretoIds = new Set(replay.run.latest_archive?.pareto_candidate_ids ?? []);
  return replay.candidates.map((candidate) => {
    const evaluation = latestEvaluation(replay, candidate.candidate_id);
    const events = eventsForCandidate(replay, candidate.candidate_id);
    const blockedByPreflight = events.some(
      (event) =>
        event.name === "discovery.candidate.quarantined" &&
        event.error_code === "discovery.preflight_rejected",
    );
    const validated = events.some(
      (event) =>
        event.name === "discovery.candidate.transitioned" && event.payload.to === "validated",
    );
    return {
      candidate,
      evaluation,
      metrics: evaluation?.canonical_metrics ?? {},
      fidelity: evaluation?.fidelity ?? null,
      preflight: blockedByPreflight ? "blocked" : validated || evaluation ? "passed" : "pending",
      gate: evaluation
        ? evaluation.hard_constraints_passed
          ? "passed"
          : "blocked"
        : "pending",
      pareto: paretoIds.has(candidate.candidate_id),
    };
  });
}

export function metricNames(rows: CandidateRow[]): string[] {
  return Array.from(new Set(rows.flatMap((row) => Object.keys(row.metrics)))).sort();
}

export function shortRef(value: string, length = 12): string {
  if (!value) return "—";
  if (value.length <= length) return value;
  return `${value.slice(0, Math.max(4, length - 5))}…${value.slice(-4)}`;
}

export function formatMetric(metric: MetricValue | undefined): string {
  if (!metric) return "—";
  const value = Number.isInteger(metric.value) ? String(metric.value) : metric.value.toFixed(4);
  return metric.unit ? `${value} ${metric.unit}` : value;
}
