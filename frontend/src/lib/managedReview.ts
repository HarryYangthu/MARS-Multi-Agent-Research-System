import type { RunDetail } from "./api";

export function managedReviewPending(run: RunDetail | null, stage: string, kind: string): boolean {
  if (run?.review_mode !== "commander") return false;
  const review = run.managed_review;
  const matching = review?.kind === kind && (run.review_generation === undefined || review.generation === run.review_generation)
    && (review.node === stage || review.node?.startsWith(`${stage}_attempt_`));
  return !matching || !["needs_user", "cancelled"].includes(review?.status || "");
}

export function managedReviewReason(run: RunDetail | null, stage: string, kind: string): string {
  const review = run?.managed_review;
  return review?.kind === kind && (run?.review_generation === undefined || review.generation === run.review_generation)
    && (review.node === stage || review.node?.startsWith(`${stage}_attempt_`)) ? review.reason || "" : "";
}
