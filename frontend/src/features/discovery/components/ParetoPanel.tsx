import { candidateRows, metricNames, shortRef } from "../selectors";
import type { DiscoveryReplayView } from "../types";
import { EmptyState } from "./Primitives";

interface Point {
  id: string;
  x: number;
  y: number;
  pareto: boolean;
}
function scaled(value: number, min: number, max: number, start: number, span: number): number {
  if (max === min) return start + span / 2;
  return start + ((value - min) / (max - min)) * span;
}

export function ParetoPanel({ replay }: { replay: DiscoveryReplayView }): JSX.Element {
  const rows = candidateRows(replay).filter((row) => row.evaluation !== null);
  const metrics = metricNames(rows).slice(0, 2);
  if (metrics.length < 2) {
    return (
      <EmptyState
        title="Pareto view needs two metrics"
        detail="The replay currently exposes fewer than two canonical metrics."
      />
    );
  }
  const [xMetric, yMetric] = metrics;
  const raw = rows.flatMap((row) => {
    const x = row.metrics[xMetric]?.value;
    const y = row.metrics[yMetric]?.value;
    return x === undefined || y === undefined
      ? []
      : [{ id: row.candidate.candidate_id, x, y, pareto: row.pareto }];
  });
  const xValues = raw.map((point) => point.x);
  const yValues = raw.map((point) => point.y);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  const yMin = Math.min(...yValues);
  const yMax = Math.max(...yValues);
  const points: Point[] = raw.map((point) => ({
    ...point,
    x: scaled(point.x, xMin, xMax, 48, 420),
    y: 220 - scaled(point.y, yMin, yMax, 12, 176),
  }));

  return (
    <div>
      <svg
        aria-label={`${xMetric} by ${yMetric} Pareto plot`}
        className="h-auto w-full overflow-visible"
        role="img"
        viewBox="0 0 500 250"
      >
        <line stroke="rgba(148,163,184,.18)" x1="48" x2="468" y1="220" y2="220" />
        <line stroke="rgba(148,163,184,.18)" x1="48" x2="48" y1="32" y2="220" />
        {[0, 1, 2, 3, 4].map((tick) => (
          <line
            key={tick}
            stroke="rgba(148,163,184,.08)"
            x1={48 + tick * 105}
            x2={48 + tick * 105}
            y1="32"
            y2="220"
          />
        ))}
        {points.map((point) => (
          <g key={point.id}>
            <circle
              cx={point.x}
              cy={point.y}
              fill={point.pareto ? "#a78bfa" : "#22d3ee"}
              fillOpacity={point.pareto ? 0.95 : 0.5}
              r={point.pareto ? 6 : 4}
              stroke={point.pareto ? "#ddd6fe" : "transparent"}
              strokeWidth="1.5"
            />
            {point.pareto ? (
              <text fill="#c4b5fd" fontSize="9" x={point.x + 8} y={point.y + 3}>
                {shortRef(point.id, 9)}
              </text>
            ) : null}
          </g>
        ))}
        <text fill="#64748b" fontSize="10" textAnchor="middle" x="258" y="244">
          {xMetric.replaceAll("_", " ")}
        </text>
        <text
          fill="#64748b"
          fontSize="10"
          textAnchor="middle"
          transform="rotate(-90 13 126)"
          x="13"
          y="126"
        >
          {yMetric.replaceAll("_", " ")}
        </text>
      </svg>
      <div className="mt-2 flex items-center gap-4 text-[10px] uppercase tracking-[0.12em] text-slate-500">
        <span className="flex items-center gap-1.5">
          <i className="h-2 w-2 rounded-full bg-violet-400" /> Pareto
        </span>
        <span className="flex items-center gap-1.5">
          <i className="h-2 w-2 rounded-full bg-cyan-400/50" /> Evaluated
        </span>
      </div>
    </div>
  );
}
