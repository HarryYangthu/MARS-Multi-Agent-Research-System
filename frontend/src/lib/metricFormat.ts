/** Preserve small nonzero measurements without changing ordinary metric display. */
export function formatMetric(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Number.isInteger(value)) return String(value);
  if (Math.abs(value) < 0.01) return value.toPrecision(4);
  return value.toFixed(2);
}
