import type {
  DynamicProjectPackUiSchema,
  ProjectPackSummary,
} from "../types";

export const syntheticRegressionPackSummaryFixture = {
  name: "synthetic_regression",
  display_name: "Synthetic Regression Discovery",
  description: "Public Project Pack fixture for dynamic-form verification.",
  domain: "regression",
  tags: ["synthetic", "model-discovery"],
  repo_path: "/workspace/synthetic-regression",
  repo_exists: true,
  pack_version: "1.0.0-fixture",
  contract_version: "project_pack.v1",
  capabilities: ["multi_agent_research", "idea_deep_discovery", "model_discovery"],
  pack_distribution: "public",
  compatibility_mode: "v31_pack",
  readiness: { status: "ready", missing: [] },
} satisfies ProjectPackSummary;

export const syntheticRegressionUiSchemaFixture = {
  type: "object",
  title: "Synthetic regression inputs",
  description: "Every field is driven by the Project Pack UI Schema.",
  required: ["dataset_kind", "feature_count", "target_metrics"],
  properties: {
    dataset_kind: {
      type: "string",
      title: "Dataset kind",
      enum: ["generated", "uploaded", "replay"],
      enumNames: ["Generated", "Uploaded", "Replay"],
      default: "generated",
    },
    feature_count: {
      type: "integer",
      title: "Feature count",
      minimum: 1,
      maximum: 512,
      default: 16,
    },
    sample_count: {
      type: "integer",
      title: "Sample count",
      minimum: 32,
      default: 2048,
    },
    target_metrics: {
      type: "array",
      title: "Target metrics",
      description: "Separate entries with commas or line breaks.",
      default: ["validation_score", "parameter_count"],
      items: { type: "string" },
    },
    freeze_baseline: {
      type: "boolean",
      title: "Freeze baseline",
      default: true,
    },
    evaluation: {
      type: "object",
      title: "Evaluation protocol",
      required: ["seed_count"],
      properties: {
        seed_count: {
          type: "integer",
          title: "Seed count",
          minimum: 1,
          default: 3,
        },
        score_tolerance: {
          type: "number",
          title: "Hard-constraint tolerance",
          default: 0.02,
        },
      },
    },
  },
} satisfies DynamicProjectPackUiSchema;
