export type TensorBoardSession = {
  key: string; project: string; run_id: string | null;
  url: string; status: string; logdirs: string[];
};
export type ExecutionDisplay = TensorBoardSession & {
  activation_id: string; phase: string; task: string; attempt: number; started_at: number;
};
export async function openTensorBoard(project: string, runId?: string): Promise<TensorBoardSession> {
  const response = await fetch("/api/tensorboard/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, run_id: runId || null }),
  });
  if (!response.ok) {
    const error: { detail?: string } = await response.json().catch(() => ({}));
    throw new Error(error.detail || "TensorBoard 暂时不可用");
  }
  return response.json();
}
export async function getExecutionDisplay(project: string): Promise<ExecutionDisplay | null> {
  const response = await fetch(`/api/tensorboard/status?project=${encodeURIComponent(project)}`, { cache: "no-store" });
  if (!response.ok) throw new Error("无法读取实验展示状态");
  const body: { active: ExecutionDisplay | null } = await response.json();
  return body.active;
}
