import type { RunDetail } from "./api";
import { latestStages } from "./runReview";

export function showCompletedCodeChanges(run: RunDetail | null): boolean {
  if (!run) return false;
  const coding = latestStages(run).find(item => item.stage === "coding");
  return !!coding && ["waiting_review", "approved", "done", "completed"].includes(coding.state);
}
