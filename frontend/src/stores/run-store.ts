import { create } from "zustand";
import type { RunDetail } from "@/lib/api";

type RunState = {
  current: RunDetail | null;
  states: Record<string, string>;
  events: { channel: string; payload: Record<string, unknown> }[];
  setCurrent: (r: RunDetail | null) => void;
  patchState: (agent: string, state: string) => void;
  pushEvent: (msg: { channel: string; payload: Record<string, unknown> }) => void;
  reset: () => void;
};

export const useRunStore = create<RunState>((set) => ({
  current: null,
  states: {},
  events: [],
  setCurrent: (r) => set({ current: r, states: r ? { ...r.states } : {} }),
  patchState: (agent, state) =>
    set((s) => ({ states: { ...s.states, [agent]: state } })),
  pushEvent: (msg) => set((s) => ({ events: [...s.events.slice(-200), msg] })),
  reset: () => set({ current: null, states: {}, events: [] }),
}));
