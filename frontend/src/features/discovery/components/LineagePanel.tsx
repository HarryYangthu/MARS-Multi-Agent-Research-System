import Link from "next/link";

import { shortRef } from "../selectors";
import type { DiscoveryReplayView } from "../types";
import { Badge, EmptyState } from "./Primitives";

export function LineagePanel({ replay }: { replay: DiscoveryReplayView }): JSX.Element {
  const candidates = [...replay.candidates].sort(
    (left, right) =>
      left.generation - right.generation || left.candidate_id.localeCompare(right.candidate_id),
  );
  if (candidates.length === 0) {
    return <EmptyState title="No lineage records" detail="Parent links appear after proposals are stored." />;
  }
  const generations = Array.from(new Set(candidates.map((item) => item.generation)));
  return (
    <div className="space-y-5">
      {generations.map((generation) => (
        <div key={generation}>
          <div className="mb-2 flex items-center gap-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">
              Generation {generation}
            </span>
            <span className="h-px flex-1 bg-white/8" />
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {candidates
              .filter((item) => item.generation === generation)
              .map((item) => (
                <Link
                  className="rounded-xl border border-white/8 bg-white/[0.025] p-3 transition hover:border-cyan-400/30 hover:bg-cyan-400/[0.04]"
                  href={`/discovery/candidates/${encodeURIComponent(item.candidate_id)}?run=${encodeURIComponent(replay.run.run_id)}`}
                  key={item.candidate_id}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs text-cyan-300">
                      {shortRef(item.candidate_id, 16)}
                    </span>
                    <Badge value={item.status} />
                  </div>
                  <p className="mt-2 truncate text-[11px] text-slate-500">
                    {item.parent_ids.length > 0
                      ? `from ${item.parent_ids.map((id) => shortRef(id, 10)).join(", ")}`
                      : "root proposal"}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-400">{item.operator}</p>
                </Link>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}
