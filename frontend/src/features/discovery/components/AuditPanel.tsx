import { shortRef } from "../selectors";
import type { DiscoveryReplayView } from "../types";

export function AuditPanel({ replay }: { replay: DiscoveryReplayView }): JSX.Element {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[900px] text-left text-xs">
        <thead className="text-[10px] uppercase tracking-[0.12em] text-slate-500">
          <tr>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Candidate</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Parent selection</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">LLM attribution</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Operator</th>
            <th className="border-b border-white/10 px-3 py-3 font-medium">Prompt / context</th>
          </tr>
        </thead>
        <tbody>
          {replay.candidates.map((candidate) => (
            <tr className="align-top" key={candidate.candidate_id}>
              <td className="border-b border-white/5 px-3 py-3 font-mono text-cyan-300">
                {shortRef(candidate.candidate_id, 16)}
              </td>
              <td className="border-b border-white/5 px-3 py-3 text-slate-400">
                {candidate.parent_ids.length > 0
                  ? candidate.parent_ids.map((id) => shortRef(id, 10)).join(", ")
                  : "root"}
              </td>
              <td className="border-b border-white/5 px-3 py-3">
                <p className="text-slate-300">{candidate.creator}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  {[candidate.model_provider, candidate.model_name].filter(Boolean).join(" / ") || "—"}
                </p>
              </td>
              <td className="border-b border-white/5 px-3 py-3 text-slate-300">
                {candidate.operator}
              </td>
              <td className="border-b border-white/5 px-3 py-3 font-mono text-[11px] text-slate-500">
                <p>{shortRef(candidate.prompt_hash, 18)}</p>
                <p className="mt-1">{shortRef(candidate.context_manifest_ref, 24)}</p>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
