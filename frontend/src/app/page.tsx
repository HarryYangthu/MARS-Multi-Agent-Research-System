"use client";

import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { EventLog } from "@/components/EventLog";
import { DataSourcePrepPanel } from "@/components/DataSourcePrepPanel";
import { HumanFeedback } from "@/components/HumanFeedback";
import { KBPanel } from "@/components/KBPanel";
import { PipelineOverview } from "@/components/PipelineOverview";
import { ProjectsPanel } from "@/components/ProjectsPanel";
import { TopBar } from "@/components/TopBar";
import { useRuntimeSnapshot } from "@/lib/dashboard";
import { useProject } from "@/lib/project";

const HANDLE_SIZE = 8;
const LAYOUT_STORAGE_KEY = "mars.dashboard.resizableLayout.v1";
const DEFAULT_LAYOUT = {
  leftWidth: 300,
  rightWidth: 360,
  dataHeight: 430,
  eventsHeight: 440,
};

const LIMITS = {
  leftMin: 220,
  leftMax: 520,
  rightMin: 290,
  rightMax: 560,
  centerMin: 520,
  dataMin: 112,
  dataMax: 560,
  pipelineMin: 260,
  eventsMin: 170,
  kbMin: 210,
};

type DashboardLayout = typeof DEFAULT_LAYOUT;
type DragPoint = {
  clientX: number;
  clientY: number;
};
type DashboardStyle = CSSProperties &
  Record<
    "--mars-left-pane" | "--mars-right-pane" | "--mars-data-pane" | "--mars-events-pane",
    string
  >;

export default function LabDashboard(): JSX.Element {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [layout, setLayout] = useState<DashboardLayout>(DEFAULT_LAYOUT);
  const mainRef = useRef<HTMLElement | null>(null);
  const dataContentRef = useRef<HTMLDivElement | null>(null);
  const { selectedProject } = useProject();
  const runtime = useRuntimeSnapshot(selectedProject);

  const mainSize = useCallback(() => {
    const rect = mainRef.current?.getBoundingClientRect();
    return {
      width: rect?.width ?? (typeof window === "undefined" ? 1440 : window.innerWidth),
      height: rect?.height ?? (typeof window === "undefined" ? 820 : window.innerHeight - 96),
    };
  }, []);

  const clampLayout = useCallback(
    (next: DashboardLayout): DashboardLayout => {
      const { width, height } = mainSize();
      const hasRightRail = width >= 1120;
      const hasLeftRail = width >= 768;
      const reservedHandles = hasLeftRail ? HANDLE_SIZE + (hasRightRail ? HANDLE_SIZE : 0) : 0;
      const availableForSidebars = Math.max(
        LIMITS.leftMin,
        width - LIMITS.centerMin - reservedHandles,
      );
      const rightMax = hasRightRail
        ? Math.min(LIMITS.rightMax, availableForSidebars - LIMITS.leftMin)
        : DEFAULT_LAYOUT.rightWidth;
      const rightWidth = hasRightRail
        ? clamp(next.rightWidth, LIMITS.rightMin, Math.max(LIMITS.rightMin, rightMax))
        : next.rightWidth;
      const leftMax = hasLeftRail
        ? Math.min(
            LIMITS.leftMax,
            width - LIMITS.centerMin - reservedHandles - (hasRightRail ? rightWidth : 0),
          )
        : DEFAULT_LAYOUT.leftWidth;
      const leftWidth = hasLeftRail
        ? clamp(next.leftWidth, LIMITS.leftMin, Math.max(LIMITS.leftMin, leftMax))
        : next.leftWidth;
      const dataMax = Math.min(LIMITS.dataMax, Math.max(LIMITS.dataMin, height - LIMITS.pipelineMin - HANDLE_SIZE));
      const eventsMax = Math.max(LIMITS.eventsMin, height - LIMITS.kbMin - HANDLE_SIZE);

      return {
        leftWidth,
        rightWidth,
        dataHeight: clamp(next.dataHeight, LIMITS.dataMin, dataMax),
        eventsHeight: clamp(next.eventsHeight, LIMITS.eventsMin, eventsMax),
      };
    },
    [mainSize],
  );

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
      if (!raw) return;
      const parsed: unknown = JSON.parse(raw);
      if (isDashboardLayout(parsed)) {
        setLayout((current) => clampLayout({ ...current, ...parsed }));
      }
    } catch {
      /* Ignore stale layout snapshots. */
    }
  }, [clampLayout]);

  useEffect(() => {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  }, [layout]);

  useEffect(() => {
    const syncLayout = () => setLayout((current) => clampLayout(current));
    syncLayout();
    window.addEventListener("resize", syncLayout);
    return () => window.removeEventListener("resize", syncLayout);
  }, [clampLayout]);

  useEffect(() => {
    const content = dataContentRef.current;
    if (!content) return;

    const syncDataHeight = (): void => {
      const panel = content.firstElementChild;
      const measuredHeight = Math.ceil(panel?.scrollHeight ?? content.scrollHeight) + 32;
      setLayout((current) => clampLayout({ ...current, dataHeight: measuredHeight }));
    };

    const syncAfterLayout = (): void => {
      window.requestAnimationFrame(syncDataHeight);
    };

    syncAfterLayout();
    const observer = new ResizeObserver(syncDataHeight);
    observer.observe(content);
    const panel = content.firstElementChild;
    if (panel) {
      observer.observe(panel);
    }
    return () => observer.disconnect();
  }, [clampLayout]);

  const layoutStyle = useMemo<DashboardStyle>(
    () => ({
      "--mars-left-pane": `${layout.leftWidth}px`,
      "--mars-right-pane": `${layout.rightWidth}px`,
      "--mars-data-pane": `${layout.dataHeight}px`,
      "--mars-events-pane": `${layout.eventsHeight}px`,
    }),
    [layout],
  );

  const resizeLeft = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const startX = event.clientX;
      const startLayout = layout;
      beginDashboardResize(event, "col-resize", ({ clientX }) => {
        setLayout(clampLayout({ ...startLayout, leftWidth: startLayout.leftWidth + clientX - startX }));
      });
    },
    [clampLayout, layout],
  );

  const resizeRight = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const startX = event.clientX;
      const startLayout = layout;
      beginDashboardResize(event, "col-resize", ({ clientX }) => {
        setLayout(clampLayout({ ...startLayout, rightWidth: startLayout.rightWidth - (clientX - startX) }));
      });
    },
    [clampLayout, layout],
  );

  const resizeData = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const startY = event.clientY;
      const startLayout = layout;
      beginDashboardResize(event, "row-resize", ({ clientY }) => {
        setLayout(clampLayout({ ...startLayout, dataHeight: startLayout.dataHeight + clientY - startY }));
      });
    },
    [clampLayout, layout],
  );

  const resizeEvents = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const startY = event.clientY;
      const startLayout = layout;
      beginDashboardResize(event, "row-resize", ({ clientY }) => {
        setLayout(clampLayout({ ...startLayout, eventsHeight: startLayout.eventsHeight + clientY - startY }));
      });
    },
    [clampLayout, layout],
  );

  return (
    <div className="grid h-screen grid-rows-[auto_1fr_auto] overflow-hidden bg-mars-bg">
      <TopBar />
      {/*
        Left (主控对话/项目) + center pipeline stay side-by-side from `md` up so a
        running pipeline never pushes the left panel below the fold; the right
        rail (events/KB) joins as a third column once there's room (>=1120px).
        Each column scrolls internally instead of forcing the page taller.
      */}
      <main
        ref={mainRef}
        className="grid min-h-0 grid-cols-1 overflow-hidden md:grid-cols-[var(--mars-left-pane)_8px_minmax(0,1fr)] min-[1120px]:grid-cols-[var(--mars-left-pane)_8px_minmax(0,1fr)_8px_var(--mars-right-pane)]"
        style={layoutStyle}
      >
        <div className="min-h-0 overflow-hidden">
          <ProjectsPanel onSelectRun={setSelectedRunId} />
        </div>
        <ResizeHandle
          label="调整项目管理栏宽度"
          orientation="vertical"
          onPointerDown={resizeLeft}
          className="hidden md:block"
        />
        <section className="grid min-h-0 grid-rows-[var(--mars-data-pane)_8px_minmax(0,1fr)] overflow-hidden border-x border-mars-border/80">
          <div className="min-h-0 overflow-auto border-b border-mars-border bg-mars-bg/40 p-4">
            <div ref={dataContentRef}>
              <DataSourcePrepPanel />
            </div>
          </div>
          <ResizeHandle
            label="调整数据准备区高度"
            orientation="horizontal"
            onPointerDown={resizeData}
          />
          <div className="min-h-0 overflow-hidden">
            <PipelineOverview
              onLinkRun={setSelectedRunId}
              selectedRunId={selectedRunId}
              stats={runtime.stats}
              readiness={runtime.readiness}
            />
          </div>
        </section>
        <ResizeHandle
          label="调整右侧信息栏宽度"
          orientation="vertical"
          onPointerDown={resizeRight}
          className="hidden min-[1120px]:block"
        />
        <aside className="hidden min-h-0 grid-rows-[var(--mars-events-pane)_8px_minmax(0,1fr)] overflow-hidden bg-mars-panel/40 min-[1120px]:grid">
          <div className="min-h-0 overflow-hidden">
            <EventLog />
          </div>
          <ResizeHandle
            label="调整事件日志和知识库高度"
            orientation="horizontal"
            onPointerDown={resizeEvents}
          />
          <div className="min-h-0 overflow-auto border-t border-mars-border">
            <KBPanel />
          </div>
        </aside>
      </main>
      <HumanFeedback />
    </div>
  );
}

function ResizeHandle({
  className = "",
  label,
  onPointerDown,
  orientation,
}: {
  className?: string;
  label: string;
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
  orientation: "horizontal" | "vertical";
}): JSX.Element {
  return (
    <div
      aria-label={label}
      aria-orientation={orientation}
      className={`mars-resize-handle mars-resize-handle-${orientation} ${className}`}
      onPointerDown={onPointerDown}
      role="separator"
      tabIndex={0}
      title={label}
    />
  );
}

function beginDashboardResize(
  event: ReactPointerEvent<HTMLDivElement>,
  cursor: "col-resize" | "row-resize",
  onMove: (point: DragPoint) => void,
): void {
  event.preventDefault();
  const handle = event.currentTarget;
  const pointerId = event.pointerId;
  const previousCursor = document.body.style.cursor;
  document.body.classList.add("mars-resizing");
  document.body.style.cursor = cursor;
  handle.setPointerCapture(pointerId);

  const move = (moveEvent: PointerEvent) => {
    onMove({ clientX: moveEvent.clientX, clientY: moveEvent.clientY });
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
    document.body.classList.remove("mars-resizing");
    document.body.style.cursor = previousCursor;
    if (handle.hasPointerCapture(pointerId)) {
      handle.releasePointerCapture(pointerId);
    }
  };

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function isDashboardLayout(value: unknown): value is DashboardLayout {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.leftWidth === "number" &&
    typeof candidate.rightWidth === "number" &&
    typeof candidate.dataHeight === "number" &&
    typeof candidate.eventsHeight === "number"
  );
}
