"use client";

export type ExpCurve = {
  values: number[];
  status: "running" | "done" | "failed";
  metrics?: Record<string, number>;
};

function Sparkline({ values, color }: { values: number[]; color: string }): JSX.Element {
  const W = 200;
  const H = 56;
  if (values.length < 2) {
    return <svg viewBox={`0 0 ${W} ${H}`} className="h-14 w-full" />;
  }
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * W;
      const y = H - ((v - min) / span) * (H - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = values[values.length - 1];
  const lastX = W;
  const lastY = H - ((last - min) / span) * (H - 6) - 3;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-14 w-full" preserveAspectRatio="none">
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        style={{ filter: `drop-shadow(0 0 3px ${color})` }}
      />
      <circle cx={lastX - 2} cy={lastY} r="2.5" fill={color} />
    </svg>
  );
}

const STATUS_COLOR: Record<string, string> = {
  running: "#f59e0b",
  done: "#34d399",
  failed: "#f87171",
};

export function CurveWall({
  curves,
  order,
}: {
  curves: Record<string, ExpCurve>;
  order: string[];
}): JSX.Element {
  const ids = order.length ? order : Object.keys(curves);
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
      {ids.map((id) => {
        const c = curves[id] ?? { values: [], status: "running" as const };
        const color = STATUS_COLOR[c.status] ?? "#94a3b8";
        const last = c.values.length ? c.values[c.values.length - 1] : null;
        return (
          <div
            key={id}
            className="rounded-lg border border-mars-border bg-mars-panel2 p-2"
            style={{ boxShadow: c.status === "running" ? `0 0 0 1px ${color}33` : undefined }}
          >
            <div className="flex items-center justify-between gap-1">
              <span className="truncate font-mono text-[10px] text-slate-300" title={id}>
                {id}
              </span>
              <span
                className="rounded px-1 py-0.5 text-[8px] uppercase"
                style={{ background: `${color}22`, color }}
              >
                {c.status}
              </span>
            </div>
            <Sparkline values={c.values} color={color} />
            <div className="mt-0.5 flex items-center justify-between font-mono text-[9px] text-slate-400">
              <span>step {c.values.length}</span>
              <span style={{ color }}>
                {last !== null ? `loss ${last.toFixed(4)}` : "…"}
              </span>
            </div>
            {c.metrics ? (
              <div className="mt-0.5 font-mono text-[9px] text-slate-500">
                RES {c.metrics.RES?.toFixed(1)}dB
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
