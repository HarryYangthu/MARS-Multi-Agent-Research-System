import Link from "next/link";

import { candidateRows, formatMetric, metricNames, shortRef } from "../selectors";
import type { DiscoveryReplayView } from "../types";
import { Badge, EmptyState } from "./Primitives";

export function CandidateTable({ replay }: { replay: DiscoveryReplayView }): JSX.Element {
  const rows = candidateRows(replay);
  const metrics = metricNames(rows).slice(0, 2);
  if (rows.length === 0) {
    return <EmptyState title="No candidates yet" detail="Candidates appear after the run starts." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[980px] border-separate border-spacing-0 text-left text-xs">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.12em] text-slate-500">
            <th className="border-b border-white/10 px-3 py-3 font-medium">Candidate</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">State</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Preflight</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Gate</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Fidelity</th>
            {metrics.map((metric) => (
              <th className="border-b border-white/10 px-3 py-3 font-medium" key={metric}>
                {metric.replaceAll("_", " ")}
              </th>
            ))}
            <th className="border-b border-white/10 px-3 py-3 font-medium">Lineage</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Operator</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="group hover:bg-white/[0.025]" key={row.candidate.candidate_id}>
              <td className="border-b border-white/5 px-3 py-3">
                <Link
                  className="font-mono text-cyan-300 transition hover:text-cyan-100"
                  href={`/discovery/candidates/${encodeURIComponent(row.candidate.candidate_id)}?run=${encodeURIComponent(replay.run.run_id)}`}
                >
                  {shortRef(row.candidate.candidate_id, 18)}
                </Link>
                {row.pareto ? <span className="ml-2 text-[10px] text-violet-300">PARETO</span> : null}
              </td>
              <td className="border-b border-white/5 px-3 py-3">
                <Badge value={row.candidate.status} />
              </td>
              <td className="border-b border-white/5 px-3 py-3">
                <Badge value={row.preflight} />
              </td>
              <td className="border-b border-white/5 px-3 py-3">
                <Badge value={row.gate} />
              </td>
              <td className="border-b border-white/5 px-3 py-3 font-mono text-slate-300">
                {row.fidelity ?? "—"}
              </td>
              {metrics.map((metric) => (
                <td className="border-b border-white/5 px-3 py-3 font-mono text-slate-300" key={metric}>
                  {formatMetric(row.metrics[metric])}
                </td>
              ))}
              <td className="border-b border-white/5 px-3 py-3 font-mono text-slate-500">
                g{row.candidate.generation} · {row.candidate.parent_ids.length} parent
              </td>
              <td className="border-b border-white/5 px-3 py-3 text-slate-400">
                {row.candidate.operator}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
