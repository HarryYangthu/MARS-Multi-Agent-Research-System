"use client";

type SidebarToggleSide = "left" | "right";

export function SidebarToggleButton({
  collapsed,
  side,
  label,
  onToggle,
  className = "",
}: {
  collapsed: boolean;
  side: SidebarToggleSide;
  label: string;
  onToggle: () => void;
  className?: string;
}): JSX.Element {
  const title = collapsed ? `展开${label}` : `收起${label}`;
  const railPosition = side === "left" ? "left-0 border-r" : "right-0 border-l";
  const cuePosition =
    side === "left"
      ? collapsed
        ? "left-[6px]"
        : "left-[2px]"
      : collapsed
        ? "right-[6px]"
        : "right-[2px]";
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={collapsed}
      onClick={onToggle}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-mars-border bg-mars-bg text-slate-400 transition hover:border-slate-500 hover:bg-mars-panel hover:text-slate-100 ${className}`}
    >
      <span className="relative block h-4 w-4 rounded-sm border border-current">
        <span className={`absolute top-0 h-full w-[5px] border-current bg-current/20 ${railPosition}`} />
        <span className={`absolute top-[6px] h-[2px] w-[5px] rounded bg-current transition-all ${cuePosition}`} />
      </span>
    </button>
  );
}
