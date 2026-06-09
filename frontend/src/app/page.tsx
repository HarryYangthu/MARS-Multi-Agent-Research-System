"use client";

import { useState } from "react";

import { EventLog } from "@/components/EventLog";
import { HumanFeedback } from "@/components/HumanFeedback";
import { KBPanel } from "@/components/KBPanel";
import { PipelineOverview } from "@/components/PipelineOverview";
import { ProjectsPanel } from "@/components/ProjectsPanel";
import { TopBar } from "@/components/TopBar";

export default function LabDashboard(): JSX.Element {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  return (
    <div className="grid h-screen grid-rows-[auto_1fr_auto]">
      <TopBar />
      <div className="grid min-h-0 grid-cols-[280px_minmax(0,1fr)_320px]">
        <ProjectsPanel onSelectRun={setSelectedRunId} />
        <PipelineOverview selectedRunId={selectedRunId} />
        <aside className="flex min-h-0 flex-col border-l border-mars-border bg-mars-panel/40">
          <div className="flex-1 min-h-0 overflow-auto">
            <EventLog />
          </div>
          <div className="overflow-auto border-t border-mars-border">
            <KBPanel />
          </div>
        </aside>
      </div>
      <HumanFeedback runId={selectedRunId} />
    </div>
  );
}
