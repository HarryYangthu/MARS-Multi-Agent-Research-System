export interface DistributionVersion {
  schema_id: "system_version.v1" | string;
  distribution: "v30-core" | "v31-wireless" | string;
  version: string;
  core_version: string;
  capabilities: string[];
  project_packs: Array<{
    project_id: string;
    pack_version: string;
    distribution: "public" | "private" | string;
    capabilities: string[];
  }>;
  adapters: string[];
}

export const V31_OPTIONAL_RUN_FIELDS = ["idea_mode", "idea_budget_profile"] as const;
