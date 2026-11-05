import { useEffect, useMemo, useState } from 'react';
import type { RunSummary } from './api';
import { readLocal, useProjects, writeLocal } from './projects';
import { eventText } from './components/ui';

export function needsAttention(run: RunSummary): boolean {
  return run.state === 'waiting_approval' || run.alerts.some((a) => a.level !== 'info' || a.code === 'awaiting_approval');
}
/** Event count of each run when it was last opened; results arriving after that count as new. */
export function seenKey(pid: string | undefined) { return `mars.seen.${pid ?? 'current'}`; }
export function markSeen(pid: string | undefined, runId: string, events: number) {
  const seen = readLocal<Record<string, number> | null>(seenKey(pid), null) ?? {};
  if (seen[runId] === events) return;
  writeLocal(seenKey(pid), { ...seen, [runId]: events });
  window.dispatchEvent(new Event('mars.inbox'));
}

export interface InboxItem { key: string; kind: 'approval' | 'result' | 'error'; run: RunSummary; message: string; at: string | null }
export function useInbox(runs: RunSummary[] | null) {
  const { current } = useProjects();
  const key = `mars.inbox.${current?.id}`;
  const [done, setDone] = useState<string[]>(() => readLocal(key, []));
  const [seen, setSeen] = useState<Record<string, number> | null>(() => readLocal(seenKey(current?.id), null));
  useEffect(() => {
    setSeen(readLocal(seenKey(current?.id), null));
    const sync = () => (setDone(readLocal(key, [])), setSeen(readLocal(seenKey(current?.id), null)));
    window.addEventListener('mars.inbox', sync); window.addEventListener('storage', sync);
    return () => { window.removeEventListener('mars.inbox', sync); window.removeEventListener('storage', sync); };
  }, [key, current?.id]);
  // First load in this browser: what already exists counts as seen, so only results that arrive later are flagged.
  useEffect(() => {
    if (runs && seen === null) {
      const initial = Object.fromEntries(runs.map((run) => [run.id, run.events]));
      writeLocal(seenKey(current?.id), initial);
      setSeen(initial);
    }
  }, [runs, seen, current?.id]);
  const items = useMemo(() => {
    const found: InboxItem[] = [];
    for (const run of runs ?? []) {
      if (run.state === 'waiting_approval') found.push({ key: `${run.id}:approval:${run.step1_round}`, kind: 'approval', run, message: run.title, at: run.last_event?.ts ?? null });
      else if (needsAttention(run)) found.push({ key: `${run.id}:error:${run.alerts.map((a) => `${a.code}:${a.since}`).join(':')}`, kind: 'error', run, message: run.alerts[0]?.detail ?? '', at: run.last_event?.ts ?? null });
      else if (seen && run.events > (seen[run.id] ?? 0) && (run.has_report || run.last_event?.stage === 'verify')) found.push({ key: `${run.id}:result:${run.last_event?.ts}`, kind: 'result', run, message: eventText(run.last_event?.message ?? '') || run.title, at: run.last_event?.ts ?? null });
    }
    return found.filter((item) => !done.includes(item.key));
  }, [runs, done, seen]);
  const dismiss = (id: string) => {
    const next = [...new Set([...done, id])].slice(-300);
    writeLocal(key, next); setDone(next); window.dispatchEvent(new Event('mars.inbox'));
  };
  return { items, dismiss };
}
