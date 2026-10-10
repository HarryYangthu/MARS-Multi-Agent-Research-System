import { codeMarkerKind, type CodeMarkerRecord, type DirectoryChangeSummary } from "@/lib/codeChangeMarkers";

const decorations = {
  added: { symbol: "+", label: "新增", color: "text-emerald-400" },
  modified: { symbol: "•", label: "修改", color: "text-orange-400" },
  deleted: { symbol: "−", label: "删除", color: "text-rose-400" },
  content: { symbol: "◇", label: "内容记录，尚无法确定增删", color: "text-amber-400" },
} as const;

export function codeChangeColor(change?: string): string {
  const kind = change ? codeMarkerKind(change) : null;
  return kind ? decorations[kind].color : "";
}

export function CodeChangeMarker({ record }: { record: CodeMarkerRecord }): JSX.Element | null {
  const kind = codeMarkerKind(record.change);
  if (!kind) return null;
  const decoration = decorations[kind];
  const state = ({ applied: "已写入", not_applied: "未写入", proposed: "待应用", recorded: "待核对" } as Record<string, string>)[record.status] ?? "待核对";
  const description = `${decoration.label} · ${state}`;
  return <span title={description} aria-label={description} data-change-kind={kind} className={`inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border border-current font-mono text-xs leading-none ${decoration.color}`}><span aria-hidden="true">{decoration.symbol}</span></span>;
}

export function DirectoryChangeMarker({ summary }: { summary?: DirectoryChangeSummary }): JSX.Element | null {
  if (!summary) return null;
  const description = `含 ${summary.total} 个文件的改动记录${summary.unapplied ? `，${summary.unapplied} 个尚未确认写入` : ""}`;
  return <span title={description} aria-label={description} data-directory-changes={summary.total} className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center text-orange-400"><span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" /></span>;
}
