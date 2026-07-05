"use client";

const STORAGE_PREFIX = "mars.selected_data_source";

export function activeDataSourceStorageKey(project: string): string {
  return `${STORAGE_PREFIX}.${project || "pimc"}`;
}

export function readActiveDataSourceId(project: string): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(activeDataSourceStorageKey(project)) ?? "";
}

export function writeActiveDataSourceId(project: string, id: string): void {
  if (typeof window === "undefined") return;
  const key = activeDataSourceStorageKey(project);
  if (id.trim()) {
    window.localStorage.setItem(key, id.trim());
  } else {
    window.localStorage.removeItem(key);
  }
}
