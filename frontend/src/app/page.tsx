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
      <main className="grid min-h-0 grid-cols-1 overflow-auto min-[1180px]:grid-cols-[300px_minmax(0,1fr)_330px]">
        <ProjectsPanel onSelectRun={setSelectedRunId} />
        <section className="flex min-h-[720px] flex-col overflow-hidden border-x border-mars-border/80 min-[1180px]:min-h-0">
          <PipelineOverview
            selectedRunId={selectedRunId}
            stats={runtime.stats}
            readiness={runtime.readiness}
          />
        </section>
        <aside className="grid min-h-[720px] grid-rows-[minmax(0,1fr)_minmax(280px,42%)] bg-mars-panel/40 min-[1180px]:min-h-0">
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
