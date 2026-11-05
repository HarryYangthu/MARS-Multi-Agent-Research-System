/** Project-declared metrics: labels, directions and the gaps the core recorded (worse_by: negative = better). */
import type { CheckGates, IterationRow, MetricsDecl, Pair, StageEntry } from './api';

export const NO_METRICS: MetricsDecl = { primary: null, fields: {} };

export function metricLabel(metrics: MetricsDecl | undefined, name: string | null | undefined): string {
  if (!name) return '—';
  const meta = metrics?.fields?.[name];
  return `${meta?.label ?? name}${meta?.unit ? ` (${meta.unit})` : ''}`;
}

export const primaryLabel = (metrics: MetricsDecl | undefined) => metricLabel(metrics, metrics?.primary);

/** Per dataset, the largest gap over the metrics the contract gates at verification (what decides the verdict). */
export function verifyGap(it: IterationRow): Pair {
  return Object.fromEntries(Object.entries(it.verify ?? {}).map(([d, row]) => {
    const gated = Object.keys(row.gates ?? {});
    const values = (gated.length ? gated : Object.keys(row.worse_by ?? {}).slice(0, 1))
      .map((m) => row.worse_by?.[m]).filter((v): v is number => typeof v === 'number');
    return [d, values.length ? Math.max(...values) : null];
  }));
}

export function failedChecks(gates: CheckGates | null | undefined): string[] {
  return (gates?.gates ?? []).filter((g) => !g.ok).map((g) => `${g.check}.${g.field}`);
}

/** "check.field = values (≤ max)" for every check gate. */
export function checkLines(gates: CheckGates | null | undefined): { text: string; ok: boolean }[] {
  return (gates?.gates ?? []).map((g) => {
    const bounds = [g.max !== undefined ? `≤ ${g.max}` : '', g.min !== undefined ? `≥ ${g.min}` : ''].filter(Boolean).join('，');
    const values = g.values.map((v) => (typeof v === 'number' ? Number(v.toPrecision(4)) : String(v))).join(' / ') || '—';
    return { text: `${g.check}.${g.field} = ${values}（${bounds}${g.stop ? '，不满足不训练' : ''}）`, ok: g.ok };
  });
}

export const entryGap = (entry: StageEntry | undefined) =>
  entry?.primary ? entry.worse_by?.[entry.primary] ?? null : null;
