export type ProjectPackCapability =
  | "multi_agent_research"
  | "idea_deep_discovery"
  | "model_discovery"
  | "wireless_overlay"
  | string;

export interface ProjectPackSummary {
  id: string;
  display_name: string;
  pack_version?: string;
  contract_version?: "project_pack.v1" | string;
  capabilities?: ProjectPackCapability[];
  readiness?: {
    status: "ready" | "blocked" | "unknown";
    missing?: string[];
  };
}

export function isLegacyProject(project: ProjectPackSummary): boolean {
  return !project.contract_version || !project.capabilities;
}
