export interface DistributionVersion {
  distribution: "v30-core" | "v31-wireless" | string;
  version: string;
  core_version: string;
  project_packs: Record<string, string>;
}

export const V31_OPTIONAL_RUN_FIELDS = ["idea_mode", "idea_budget_profile"] as const;
