import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, setApiProject, type Project } from './api';

export function readLocal<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') as T ?? fallback; } catch { return fallback; }
}
export function writeLocal(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage may be disabled */ }
}
interface ProjectsContext {
  current: Project | null; projects: Project[]; defaultParent: string; error: string;
  select: (id: string) => void; reload: (selectId?: string) => Promise<void>;
}
const Context = createContext<ProjectsContext | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentId, setCurrentId] = useState('');
  const [defaultParent, setParent] = useState('');
  const [error, setError] = useState('');
  const reload = useCallback(async (selectId?: string) => {
    try {
      const found = await api.projects();
      // First start of a fresh install (only the default workspace and the demo): open the demo's real results.
      const demo = found.projects.length === 2 ? found.projects.find((p) => p.demo)?.id : undefined;
      const bundled = found.projects.find((p) => p.preferred)?.id;
      const fallback = bundled ?? demo ?? 'current';
      const preferred = selectId ?? readLocal<string>('mars.project', fallback);
      const id = found.projects.some((p) => p.id === preferred) ? preferred : fallback;
      setApiProject(id);
      setProjects(found.projects); setParent(found.default_parent); setCurrentId(id); setError('');
      writeLocal('mars.project', id);
    } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  const select = (id: string) => {
    if (!projects.some((p) => p.id === id)) return;
    setApiProject(id); setCurrentId(id); writeLocal('mars.project', id);
    window.location.hash = '#/';
  };
  const found = projects.find((p) => p.id === currentId) ?? null;
  // The demo project is read-only: everything that starts or changes work treats it as not connected.
  const current = found?.demo ? { ...found, connected: false } : found;
  return <Context.Provider value={{ current, projects, defaultParent, error, select, reload }}>{children}</Context.Provider>;
}
export function useProjects() {
  const context = useContext(Context);
  if (!context) throw new Error('useProjects outside ProjectProvider');
  return context;
}

export interface Visit { id: string; route?: string; iteration?: number; viewer?: string; viewedAt: string; events: number }
export function visitKey(pid: string) { return `mars.visit.${pid}`; }
