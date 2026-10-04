"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getRun } from "@/lib/api";
import { researchRunConversationUrl } from "@/lib/runConversation";

export function RunConversationRedirect({ runId }: { runId: string }): JSX.Element {
  const router = useRouter();
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    void getRun(runId).then(run => { if (active) router.replace(researchRunConversationUrl(run)); })
      .catch(() => { if (active) setError("暂时无法打开研究任务，请检查服务连接后重试。"); });
    return () => { active = false; };
  }, [runId, router, revision]);
  return <div className="flex min-h-dvh items-center justify-center bg-mars-bg p-8 text-center text-sm text-slate-400">{error ? <div role="alert"><p>{error}</p><button type="button" className="mt-4 text-indigo-300 underline" onClick={() => setRevision(value => value + 1)}>重新打开</button></div> : <p role="status">正在打开研究对话…</p>}</div>;
}
