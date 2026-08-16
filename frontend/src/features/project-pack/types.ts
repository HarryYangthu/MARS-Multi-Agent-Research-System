import type { DistributionVersion } from "@/contracts/v31/compatibility";
import type {
  ProjectPackSummary as ContractProjectPackSummary,
  ProjectPackUiSchema as ContractProjectPackUiSchema,
} from "@/contracts/v31/project-pack";
import type { IdeaBudgetProfile, IdeaMode } from "@/contracts/v31/idea-discovery";

export type ProjectPackSummary = ContractProjectPackSummary;
export type SystemVersion = DistributionVersion;
export type { IdeaBudgetProfile, IdeaMode };

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export type ProjectPackFieldType =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "array"
  | "object";

export interface ProjectPackFieldSchema {
  type: ProjectPackFieldType;
  title?: string;
  description?: string;
  default?: JsonValue;
  enum?: JsonPrimitive[];
  enumNames?: string[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
  widget?: string;
  required?: string[];
  properties?: Record<string, ProjectPackFieldSchema>;
  items?: ProjectPackFieldSchema;
}

export interface DynamicProjectPackUiSchema
  extends Omit<ContractProjectPackUiSchema, "properties"> {
  properties: Record<string, ProjectPackFieldSchema>;
}

export interface ProjectPackValidationIssue {
  path: string;
  message: string;
}

export interface CreateV31RunInput {
  task: string;
  project: string;
  userRequest: string;
  ideaMode: IdeaMode;
  budgetProfile: IdeaBudgetProfile;
  projectInputs: JsonObject;
  compatibilityMode: boolean;
}

export interface CreatedRun {
  run_id: string;
  project: string;
  task: string;
  entrypoint: string;
  created_at: string;
  states: Record<string, string>;
  graph: Record<string, unknown>;
}
