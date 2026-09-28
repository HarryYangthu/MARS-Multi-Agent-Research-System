import { STAGE_ORDER, type Stage, type RunDetail } from "./api";
import { effectiveNodeState } from "./researchActivity";

export function latestStages(run: RunDetail): { stage: Stage; state: string }[] {
  return STAGE_ORDER.filter(stage => Object.keys(run.states).some(key => key === stage || key.startsWith(`${stage}_attempt_`))).map(stage => {
    const keys = Object.keys(run.states).filter(key => key === stage || key.startsWith(`${stage}_attempt_`));
    keys.sort((a,b) => Number(a.split("_attempt_")[1] || 1) - Number(b.split("_attempt_")[1] || 1));
    return { stage, state: effectiveNodeState(run.states[keys[keys.length - 1]], run.status) };
  });
}
export function reviewFocus(run: RunDetail, requested: string): Stage | null {
  const stages = latestStages(run);
  const explicit = stages.find(item => item.stage === requested);
  if (explicit) return explicit.stage;
  for (const state of ["waiting_review", "failed", "running", "interrupted"]) {
    const current = stages.find(item => item.state === state);
    if (current) return current.stage;
  }
  return [...stages].reverse().find(item => ["done", "approved"].includes(item.state))?.stage
    ?? stages.find(item => item.stage === run.entrypoint)?.stage ?? stages.find(item => item.state !== "skipped")?.stage ?? null;
}
export function artifactBody(text: string): string {
  return text.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "");
}
