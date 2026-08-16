import type { BudgetLimits, BudgetSnapshot, BudgetUsage } from "../types";

const RESOURCE_LABELS: Array<{
  key: keyof BudgetUsage;
  label: string;
}> = [
  { key: "proposals", label: "Proposals" },
  { key: "llm_tokens", label: "LLM tokens" },
  { key: "gpu_seconds", label: "Compute seconds" },
  { key: "wall_seconds", label: "Wall seconds" },
  { key: "api_cost", label: "API cost" },
];

function ratio(used: number, limit: number): number {
  if (limit <= 0) return used > 0 ? 100 : 0;
  return Math.min(100, (used / limit) * 100);
}
function display(key: keyof BudgetUsage, value: number): string {
  if (key === "api_cost") return `$${value.toFixed(3)}`;
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);
}

export function BudgetPanel({ budget }: { budget: BudgetSnapshot }): JSX.Element {
  return (
    <div className="space-y-4">
      {RESOURCE_LABELS.map(({ key, label }) => {
        const used = budget.used[key];
        const limit = budget.limits[key as keyof BudgetLimits];
        if (typeof limit !== "number") return null;
        const percent = ratio(used, limit);
        return (
          <div key={key}>
            <div className="mb-1.5 flex items-center justify-between text-xs">
              <span className="text-slate-400">{label}</span>
              <span className="font-mono text-slate-300">
                {display(key, used)} / {display(key, limit)}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
              <div
                className={`h-full rounded-full ${percent >= 90 ? "bg-rose-400" : percent >= 70 ? "bg-amber-400" : "bg-cyan-400"}`}
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
        );
      })}
      <div className="flex items-center justify-between border-t border-white/8 pt-3 text-xs text-slate-500">
        <span>Parallel slots</span>
        <span className="font-mono text-slate-300">
          {budget.active_slots.length} / {budget.limits.max_parallel}
        </span>
      </div>
    </div>
  );
}
