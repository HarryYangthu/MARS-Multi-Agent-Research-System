export interface DiscoveryCandidateView {
  candidate_id: string;
  parent_ids: string[];
  generation: number;
  status: string;
  fidelity: string;
  canonical_metrics: Record<string, number>;
  quarantined: boolean;
}

export interface DiscoveryRunView {
  run_id: string;
  project: string;
  status: string;
  iteration: number;
  stop_reason?: string;
  candidates: DiscoveryCandidateView[];
  budget: Record<string, number>;
}
