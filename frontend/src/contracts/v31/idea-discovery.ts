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
  testable_predictions?: string[];
  constraints?: string[];
  operator?: string;
}

export interface ReflectionView {
  reflection_id: string;
  hypothesis_id: string;
  correctness: string;
  novelty: string;
  falsifiability: string;
  assumptions: string[];
  failure_modes: string[];
  blockers: string[];
}

export interface PairwiseMatchView {
  match_id: string;
  left_id: string;
  right_id: string;
  outcome: "left" | "right" | "draw";
  reason: string;
  left_rating_after: number;
  right_rating_after: number;
}

export interface IdeaDiscoveryView {
  run_id: string;
  status: string;
  round_index: number;
  hypotheses: HypothesisView[];
  reflections?: ReflectionView[];
  matches?: PairwiseMatchView[];
  finalist_ids: string[];
  selected_id?: string;
  proposal_ref?: string;
  meta_review?: Record<string, unknown>;
}
