export type IdeaMode = "auto" | "fast" | "deep";
export type IdeaBudgetProfile = "fast" | "balanced" | "thorough";

export interface HypothesisView {
  hypothesis_id: string;
  statement: string;
  mechanism: string;
  cluster_id: string;
  elo: number;
  blocked: boolean;
  parent_ids: string[];
  evidence_refs: string[];
}

export interface IdeaDiscoveryView {
  run_id: string;
  status: string;
  round_index: number;
  hypotheses: HypothesisView[];
  finalist_ids: string[];
  selected_id?: string;
  proposal_ref?: string;
  meta_review?: Record<string, unknown>;
}
