export type ProjectPackCapability =
  | "multi_agent_research"
  | "idea_deep_discovery"
  | "model_discovery"
  | "wireless_overlay"
  | string;

export interface ProjectPackSummary {
  name: string;
  display_name: string;
  description: string;
  domain: string;
  tags: string[];
  repo_path: string;
  repo_exists: boolean;
  pack_version: string | null;
  contract_version: "project_pack.v1" | string | null;
  capabilities: ProjectPackCapability[];
  pack_distribution: "public" | "private" | null;
  compatibility_mode: "v30_legacy" | "v31_pack";
  readiness?: {
    status: "ready" | "blocked" | "unknown";
    missing?: string[];
  };
}

export function isLegacyProject(project: ProjectPackSummary): boolean {
  return project.compatibility_mode === "v30_legacy" || !project.contract_version;
}

export interface ProjectPackUiSchema {
  type: "object";
  title?: string;
  description?: string;
  required?: string[];
  properties: Record<string, Record<string, unknown>>;
}
