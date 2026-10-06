export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = "ApiError"; }
}

// A known refusal is actionable. Only an unknown mutation outcome stays locked.
export function reviewResultUncertain(cause: unknown, submitted: boolean): boolean {
  return submitted && (!(cause instanceof ApiError) || cause.status >= 500 || cause.status === 408);
}
