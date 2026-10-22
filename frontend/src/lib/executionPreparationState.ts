import type { ExecutionConfiguration } from "./executionReview";

export type PreparationStatus = "unprepared" | "preparing" | "ready" | "failed" | "stale" | "interrupted";
export type ValidationStatus = "required" | "running" | "passed" | "failed" | "stale" | "unverified";
export type ExecutionPreparation = {
  run_id: string; project: string; node: string; operation_id: string;
  status: PreparationStatus; token: string; completed: number; total: number;
  validation: { name: string; status: ValidationStatus; configured?: boolean; error?: string }[];
  confirmed: boolean; launch_state: string; error?: string;
  confirmation_status: "unconfirmed" | "confirming" | "confirmed" | "failed";
  confirmation_error?: string;
  configuration?: ExecutionConfiguration;
};
export type PreparationMutation = "prepare" | "confirm";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function checkedExecutionPreparation(value: unknown, runId: string, project: string): ExecutionPreparation {
  const statuses: readonly string[] = ["unprepared", "preparing", "ready", "failed", "stale", "interrupted"];
  const validations: readonly string[] = ["required", "running", "passed", "failed", "stale", "unverified"];
  if (!record(value) || value.run_id !== runId || value.project !== project
    || typeof value.node !== "string" || typeof value.operation_id !== "string"
    || typeof value.status !== "string" || !statuses.includes(value.status)
    || typeof value.token !== "string" || value.token !== "" && !/^[a-f0-9]{64}$/.test(value.token)
    || !Number.isSafeInteger(value.completed) || !Number.isSafeInteger(value.total)
    || Number(value.completed) < 0 || Number(value.total) < Number(value.completed)
    || typeof value.confirmed !== "boolean" || typeof value.launch_state !== "string"
    || typeof value.confirmation_status !== "string" || !["unconfirmed", "confirming", "confirmed", "failed"].includes(value.confirmation_status)
    || value.confirmation_error !== undefined && typeof value.confirmation_error !== "string"
    || value.error !== undefined && typeof value.error !== "string" || !Array.isArray(value.validation)
    || value.validation.some(item => !record(item) || typeof item.name !== "string"
      || typeof item.status !== "string" || !validations.includes(item.status)
      || item.configured !== undefined && typeof item.configured !== "boolean"
      || item.error !== undefined && typeof item.error !== "string")) {
    throw new Error("运行前检查的状态不完整，请重新读取。");
  }
  if (value.status === "ready" && !value.token) throw new Error("运行前检查缺少配置标识，请重新检查。");
  if (value.configuration !== undefined) {
    const config = value.configuration;
    if (!record(config) || config.run_id !== runId || config.project !== project
      || config.node !== value.node || typeof config.token !== "string"
      || value.token && config.token !== value.token || typeof config.visible !== "boolean"
      || typeof config.state !== "string" || typeof config.can_confirm !== "boolean"
      || typeof config.confirmed !== "boolean" || typeof config.launch_ready !== "boolean"
      || !Array.isArray(config.experiments) || !Array.isArray(config.source_configs)
      || !Array.isArray(config.jobs) || !Array.isArray(config.blockers) || !Array.isArray(config.warnings)
      || !record(config.defaults) || !record(config.budget)) {
      throw new Error("运行前检查与当前任务配置不一致，请重新检查。");
    }
  }
  return value as ExecutionPreparation;
}

// Ordering only: a late poll cannot replace the result of a newer POST. A failed
// transport releases the request slot while the outcome remains separately unknown.
export class PreparationRequestOrder {
  private version = 0;
  private mutation: number | null = null;
  beginRead(): number { return this.version; }
  acceptsRead(ticket: number): boolean { return ticket === this.version; }
  beginMutation(): number | null {
    if (this.mutation !== null) return null;
    this.mutation = ++this.version;
    return this.mutation;
  }
  acceptsMutation(ticket: number): boolean { return this.mutation === ticket && this.version === ticket; }
  finishMutation(ticket: number): boolean {
    if (!this.acceptsMutation(ticket)) return false;
    this.mutation = null;
    this.version++;
    return true;
  }
  invalidate(): void { this.version++; this.mutation = null; }
}

export function preparationCanConfirm(preparation: ExecutionPreparation | null, acknowledged: string, stale: boolean, launchBlocked: boolean): boolean {
  return Boolean(preparation?.status === "ready" && preparation.token && preparation.token === acknowledged
    && preparation.launch_state === "approved"
    && preparation.configuration?.token === preparation.token && preparation.configuration.can_confirm
    && preparation.confirmation_status !== "confirming" && !preparation.confirmed && !stale && !launchBlocked);
}

export function preparationCanRetryConfirmation(preparation: ExecutionPreparation | null, submittedToken: string): boolean {
  return Boolean(submittedToken && preparation?.token === submittedToken && preparation.status === "ready"
    && preparation.confirmation_status !== "confirming" && preparation.launch_state === "approved");
}

export function preparationSummary(preparation: ExecutionPreparation | null): string {
  if (!preparation) return "正在读取运行前检查状态";
  if (preparation.status === "preparing") return preparation.total === 0 ? "正在读取已批准的实验配置"
    : `运行前检查中：${preparation.validation.filter(item => item.status === "passed").length} / ${preparation.total} 组已通过`;
  if (preparation.status === "ready") return preparation.validation.length && preparation.validation.every(item => item.status === "passed")
    ? "所有实验的运行前检查已通过" : "运行前准备已完成，请核对各组检查状态";
  return ({ unprepared: "运行前检查尚未开始", failed: "运行前检查未通过", stale: "配置或环境已变化，需重新检查", interrupted: "运行前检查已中断，可重新检查" })[preparation.status];
}
