"use client";

import { useState } from "react";

import { EventLog } from "@/components/EventLog";
import { HumanFeedback } from "@/components/HumanFeedback";
import { KBPanel } from "@/components/KBPanel";
import { PipelineOverview } from "@/components/PipelineOverview";
import { ProjectsPanel } from "@/components/ProjectsPanel";
import { TopBar } from "@/components/TopBar";
import { useRuntimeSnapshot } from "@/lib/dashboard";
import { useProject } from "@/lib/project";

export default function LabDashboard(): JSX.Element {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const { selectedProject } = useProject();
  const runtime = useRuntimeSnapshot(selectedProject);

  return (
    <div className="grid h-screen grid-rows-[auto_1fr_auto] overflow-hidden bg-mars-bg">
      <TopBar />
      {/*
        Left (主控对话/项目) + center pipeline stay side-by-side from `md` up so a
        running pipeline never pushes the left panel below the fold; the right
        rail (events/KB) joins as a third column once there's room (>=1120px).
        Each column scrolls internally instead of forcing the page taller.
      */}
      <main className="grid min-h-0 grid-cols-1 overflow-y-auto md:grid-cols-[260px_minmax(0,1fr)] min-[1120px]:grid-cols-[300px_minmax(0,1fr)_330px]">
        <ProjectsPanel onSelectRun={setSelectedRunId} />
        <section className="flex min-h-[60vh] flex-col overflow-hidden border-x border-mars-border/80 md:min-h-0">
          <PipelineOverview
            selectedRunId={selectedRunId}
            stats={runtime.stats}
            readiness={runtime.readiness}
          />
        </section>
        <aside className="hidden min-h-0 grid-rows-[minmax(0,1fr)_minmax(280px,42%)] bg-mars-panel/40 min-[1120px]:grid">
          <div className="min-h-0 overflow-hidden">
            <EventLog />
          </div>
          <div className="min-h-0 overflow-auto border-t border-mars-border">
            <KBPanel />
          </div>
        </aside>
      </main>
      <HumanFeedback />
    </div>
  );
}
