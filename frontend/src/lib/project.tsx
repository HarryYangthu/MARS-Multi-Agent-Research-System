"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { listProjects, type ProjectSummary } from "@/lib/api";

const STORAGE_KEY = "mars.selected_project";
const DEFAULT_PROJECT = "moe-pimc";

type ProjectContextValue = {
  selectedProject: string;
  projects: ProjectSummary[];
  loading: boolean;
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

  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved) {
      setSelectedProjectState(saved);
    }
  }, []);

  async function refreshProjects(): Promise<void> {
    setLoading(true);
    try {
      const next = await listProjects();
      setProjects(next);
      setSelectedProjectState((current) => {
        if (next.length === 0) return current || DEFAULT_PROJECT;
        if (next.some((project) => project.name === current)) return current;
        const fallback =
          next.find((project) => project.name === DEFAULT_PROJECT)?.name ?? next[0].name;
        if (typeof window !== "undefined") {
          window.localStorage.setItem(STORAGE_KEY, fallback);
        }
        return fallback;
      });
    } catch {
      setProjects([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshProjects();
  }, []);

  function setSelectedProject(project: string): void {
    const normalized = project.trim() || DEFAULT_PROJECT;
    setSelectedProjectState(normalized);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, normalized);
    }
  }

  const value = useMemo(
    () => ({
      selectedProject,
      projects,
      loading,
      setSelectedProject,
      refreshProjects,
    }),
    [loading, projects, selectedProject],
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
