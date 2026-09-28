"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { listProjects, type ProjectSummary } from "@/lib/api";

const STORAGE_KEY = "mars.selected_project";
const DEFAULT_PROJECT = "";

type ProjectContextValue = {
  selectedProject: string;
  projects: ProjectSummary[];
  loading: boolean;
  error: string;
  setSelectedProject: (project: string) => void;
  refreshProjects: () => Promise<void>;
};

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  const [selectedProject, setSelectedProjectState] = useState(DEFAULT_PROJECT);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved) setSelectedProjectState(saved);
    } catch { /* The current session works without browser storage. */ }
  }, []);

  const refreshProjects = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const next = await listProjects();
      setProjects(next);
      setError("");
      setSelectedProjectState((current) => {
        if (next.length === 0) return current || DEFAULT_PROJECT;
        if (next.some((project) => project.name === current)) return current;
        const fallback =
          next[0].name;
        if (typeof window !== "undefined") {
          try { window.localStorage.setItem(STORAGE_KEY, fallback); } catch { /* session only */ }
        }
        return fallback;
      });
    } catch {
      setError("无法加载项目，请检查本地服务连接后重试。已有列表可能不是最新状态。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  const setSelectedProject = useCallback((project: string): void => {
    const normalized = project.trim() || DEFAULT_PROJECT;
    setSelectedProjectState(normalized);
    if (typeof window !== "undefined") {
      try { window.localStorage.setItem(STORAGE_KEY, normalized); } catch { /* session only */ }
    }
  }, []);

  const value = useMemo(
    () => ({
      selectedProject,
      projects,
      loading,
      error,
      setSelectedProject,
      refreshProjects,
    }),
    [loading, error, projects, selectedProject, setSelectedProject, refreshProjects],
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectContextValue {
  const value = useContext(ProjectContext);
  if (!value) {
    throw new Error("useProject must be used inside ProjectProvider");
  }
  return value;
}
