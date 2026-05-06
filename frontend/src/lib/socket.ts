// Native WebSocket subscription for run events.
// We're not using socket.io on the wire because the backend uses FastAPI's
// raw WebSocket endpoints — keep it simple.

import { WS_BASE } from "@/lib/api";

export type WSMessage = { channel: string; payload: Record<string, unknown> };

export type WSHandler = (msg: WSMessage) => void;

export function openRunSocket(runId: string, onMessage: WSHandler): () => void {
  const url = `${WS_BASE}/ws/runs/${runId}`;
  const ws = new WebSocket(url);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      // ignore malformed
    }
  };
  return () => {
    ws.close();
  };
}

export function openExperimentSocket(
  runId: string,
  expId: string,
  onMessage: WSHandler,
): () => void {
  const url = `${WS_BASE}/ws/runs/${runId}/experiment/${expId}`;
  const ws = new WebSocket(url);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      /* ignore */
    }
  };
  return () => ws.close();
}
