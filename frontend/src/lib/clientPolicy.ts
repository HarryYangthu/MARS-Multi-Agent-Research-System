// These public, non-secret values are compiled from configs/frontend.yaml.
export const CLIENT_POLICY = {
  requestTimeoutMs: Number(process.env.NEXT_PUBLIC_MARS_REQUEST_TIMEOUT_MS),
  connectionTestTimeoutMs: Number(process.env.NEXT_PUBLIC_MARS_CONNECTION_TEST_TIMEOUT_MS),
  controlRefreshMs: Number(process.env.NEXT_PUBLIC_MARS_CONTROL_REFRESH_MS),
  activityClockMs: Number(process.env.NEXT_PUBLIC_MARS_ACTIVITY_CLOCK_MS),
  readinessRefreshMs: Number(process.env.NEXT_PUBLIC_MARS_READINESS_REFRESH_MS),
  maxContractBytes: Number(process.env.NEXT_PUBLIC_MARS_MAX_CONTRACT_BYTES),
};

export async function boundedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const deadline = AbortSignal.timeout(CLIENT_POLICY.requestTimeoutMs);
  const signal = init.signal ? AbortSignal.any([deadline, init.signal]) : deadline;
  return fetch(input, { ...init, signal });
}

export function isUncertainRequestError(error: unknown): boolean {
  return error instanceof TypeError || error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError");
}
