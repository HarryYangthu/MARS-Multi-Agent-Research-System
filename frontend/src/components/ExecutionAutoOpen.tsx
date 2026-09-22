"use client";

import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useProject } from "@/lib/project";
import { getRun } from "@/lib/api";
import { getExecutionDisplay } from "@/lib/tensorboard";

/** Open once per approved execution, including after a brief disconnect. */
export function ExecutionAutoOpen(): null {
  const { selectedProject } = useProject();
  const router = useRouter();
  const pathname = usePathname();
  const seen = useRef(new Set<string>());
  const openedAt = useRef(Date.now() / 1000);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let project = selectedProject;
    const poll = async (): Promise<void> => {
      try {
        const active = await getExecutionDisplay(project);
        if (stopped || !active || (active.phase !== "running" && active.started_at < openedAt.current)) return;
        const key = `mars.tensorboard.opened.${active.activation_id}`;
        if (seen.current.has(key)) return;
        let opened = false;
        try { opened = sessionStorage.getItem(key) === "1"; } catch { /* storage can be unavailable */ }
        seen.current.add(key);
        if (opened) return;
        try { sessionStorage.setItem(key, "1"); } catch { /* use in-memory deduplication */ }
        router.push(`/?project=${encodeURIComponent(active.project)}&run=${encodeURIComponent(active.run_id || "")}`);
      } catch { /* backend restart: retry without interrupting the page */ }
      finally { if (!stopped) timer = setTimeout(() => void poll(), 2000); }
    };
    const match = /^\/runs\/([^/]+)/.exec(pathname || "");
    if (match && match[1] !== "new") {
      void getRun(decodeURIComponent(match[1])).then((run) => { project = run.project; })
        .catch(() => undefined).finally(() => { if (!stopped) void poll(); });
    } else { void poll(); }
    return () => { stopped = true; clearTimeout(timer); };
  }, [selectedProject, pathname, router]);
  return null;
}
