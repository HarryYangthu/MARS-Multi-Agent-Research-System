export interface CanonicalMetricView {
  value: number;
  unit: string;
  direction: "minimize" | "maximize";
}

export interface DiscoveryCandidateView {
  candidate_id: string;
  parent_ids: string[];
  generation: number;
  status: string;
  fidelity: string;
  canonical_metrics: Record<string, CanonicalMetricView>;
  quarantined: boolean;
  operator?: string;
  model_provider?: string;
  model_name?: string;
  diff_ref?: string;
  execution_ref?: string;
  evidence_refs?: string[];
}

export interface DiscoveryRunView {
  run_id: string;
  project: string;
  status: string;
  iteration: number;
  stop_reason?: string;
  candidates: DiscoveryCandidateView[];
  budget: Record<string, number>;
  pareto_candidate_ids?: string[];
  niche_elites?: Record<string, string>;
}

export interface SelectionAuditView {
  selection_id: string;
  kind: "parent" | "llm" | "operator";
  candidates: string[];
  probabilities: number[];
  selected: string;
  seed: number;
  reason: string;
}
