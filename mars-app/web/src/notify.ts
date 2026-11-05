import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

const POLL_MS = 60_000;
const SEEN_KEY = 'mars.alerts.seen';

function loadSeen(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
}

function saveSeen(seen: Set<string>) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...seen].slice(-500)));
  } catch {
    /* storage unavailable: notifications may repeat after a reload */
  }
}

const supported = typeof window !== 'undefined' && 'Notification' in window;

/**
 * Watches all runs while any workbench tab is open: shows the number of runs needing attention in the tab title and
 * raises a system notification for each error/warning alert that appeared after the page was opened.
 */
export function useAlertNotifications() {
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(
    supported ? Notification.permission : 'unsupported',
  );
  const first = useRef(true);

  useEffect(() => {
    let alive = true;
    const baseTitle = () => (document.documentElement.lang === 'en' ? 'MARS Research Workbench' : 'MARS 研究工作台');
    const check = async () => {
      try {
        const runs = await api.runs();
        if (!alive) return;
        const urgent = runs.filter((r) => r.alerts.some((a) => a.level !== 'info'));
        document.title = urgent.length ? `(${urgent.length}) ${baseTitle()}` : baseTitle();
        const seen = loadSeen();
        for (const run of runs) {
          for (const alert of run.alerts) {
            if (alert.level === 'info') continue;
            const key = `${run.id}|${alert.code}|${alert.since ?? ''}`;
            if (seen.has(key)) continue;
            seen.add(key);
            // Alerts already present when the page opens are visible as banners; only new ones notify.
            if (!first.current && supported && Notification.permission === 'granted') {
              const note = new Notification(`MARS · ${run.id}`, { body: alert.title, tag: key });
              note.onclick = () => {
                window.focus();
                window.location.hash = `#/runs/${encodeURIComponent(run.id)}`;
              };
            }
          }
        }
        saveSeen(seen);
        first.current = false;
      } catch {
        /* backend unreachable: the pages show their own error */
      }
    };
    check();
    const timer = window.setInterval(check, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.title = baseTitle();
    };
  }, []);

  const request = useCallback(async () => {
    if (!supported) return;
    setPermission(await Notification.requestPermission());
  }, []);

  return { permission, request };
}
