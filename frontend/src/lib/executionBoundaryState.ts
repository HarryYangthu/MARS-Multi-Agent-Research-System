export type ExecutionBoundary = {
  run_id: string;
  project: string;
  stop_after_execution: boolean;
  can_update: boolean;
  reason?: string;
};

export function checkedExecutionBoundary(value: unknown, runId: string, project: string): ExecutionBoundary {
  if (typeof value !== "object" || value === null || !("run_id" in value) || value.run_id !== runId
      || !("project" in value) || value.project !== project
      || !("stop_after_execution" in value) || typeof value.stop_after_execution !== "boolean"
      || !("can_update" in value) || typeof value.can_update !== "boolean"
      || "reason" in value && value.reason !== undefined && typeof value.reason !== "string") {
    throw new Error("未能核对当前任务的暂停设置，请刷新重试。");
  }
  return {
    run_id: runId, project,
    stop_after_execution: value.stop_after_execution,
    can_update: value.can_update,
    ...("reason" in value && typeof value.reason === "string" ? { reason: value.reason } : {}),
  };
}

/** Order reads and saves independently of the scientific launch preview. */
export class BoundaryRequestOrder {
  private version = 0;
  private saving = false;

  beginRead(): number | null {
    return this.saving ? null : ++this.version;
  }

  acceptsRead(ticket: number): boolean {
    return !this.saving && this.version === ticket;
  }

  beginSave(): number | null {
    if (this.saving) return null;
    this.saving = true;
    return ++this.version;
  }

  acceptsSave(ticket: number): boolean {
    return this.saving && this.version === ticket;
  }

  finishSave(ticket: number): boolean {
    if (!this.acceptsSave(ticket)) return false;
    this.saving = false;
    return true;
  }

  invalidate(): void {
    this.version++;
    this.saving = false;
  }
}
