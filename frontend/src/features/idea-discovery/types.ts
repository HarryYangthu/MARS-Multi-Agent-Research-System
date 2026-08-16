import type { DistributionVersion } from "@/contracts/v31/compatibility";
import type {
  HypothesisView,
  IdeaBudgetProfile,
  IdeaMode,
  PairwiseMatchView,
  ReflectionView,
} from "@/contracts/v31/idea-discovery";

export type { IdeaBudgetProfile, IdeaMode };
export type SystemVersion = DistributionVersion;

export interface DiscoveryHypothesis extends HypothesisView {
  round_index: number;
  uncertainty?: string;
}

export interface DiscoveryReflection extends ReflectionView {
  evidence_refs: string[];
}

export interface DiscoveryMatch extends PairwiseMatchView {
  round_index: number;
  evidence_refs: string[];
  left_rating_before: number;
  right_rating_before: number;
}

export interface ProximityEdgeView {
  left_id: string;
  right_id: string;
  similarity: number;
  exact_duplicate: boolean;
}

export interface ProximityGraphView {
  round_index: number;
  clusters: Record<string, string[]>;
  edges: ProximityEdgeView[];
}

export interface MetaReviewView {
  meta_review_id: string;
  round_index: number;
  recurring_errors: string[];
  successful_patterns: string[];
  evidence_gaps: string[];
  unexplored_regions: string[];
  next_round_guidance: string[];
}

export interface DebateView {
  status: string;
  summary: string;
  disagreements: string[];
  evidence_gaps: string[];
}

export interface DiscoveryConfigView {
  budget_profile: IdeaBudgetProfile | string;
  initial_hypotheses: number;
  evolution_rounds: number;
  max_pairwise_matches: number;
  top_k: number;
}

export interface IdeaDiscoverySnapshot {
  run_id: string;
  project: string;
  status: string;
  round_index: number;
  backend_mode: string;
  config: DiscoveryConfigView | null;
  hypotheses: DiscoveryHypothesis[];
  reflections: DiscoveryReflection[];
  matches: DiscoveryMatch[];
  proximity_graphs: ProximityGraphView[];
  meta_reviews: MetaReviewView[];
  debate: DebateView | null;
  finalist_ids: string[];
  selected_id: string;
  proposal_ref: string;
}

export type DiscoveryStage =
  | "generation"
  | "reflection"
  | "elo"
  | "debate"
  | "proximity"
  | "evolution"
  | "meta-review";

export interface HypothesisMutationInput {
  actor: string;
  reason: string;
}

export interface HypothesisEditInput extends HypothesisMutationInput {
  statement: string;
}

export interface HypothesisCreateInput extends HypothesisMutationInput {
  statement: string;
}

export type HypothesisCreateAuditRecord = Record<string, unknown>;
