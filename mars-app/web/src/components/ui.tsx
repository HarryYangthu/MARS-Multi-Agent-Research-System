import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import type { Pair, RunAlert, RunState } from '../api';
import { useT } from '../prefs';

/** Alert titles come from the server in Chinese; by code they are rebuilt in the interface language. */
const ALERT_TITLE: Record<string, string> = {
  model_quota: '模型余额不足，研究已停滞',
  model_api: '模型服务异常，研究已停滞',
  stalled: '可能停滞：{hours} 小时没有新事件',
  stopped: '进程已退出，研究没有在推进',
  awaiting_approval: '等待你确认进入 Step2 的方案（已等 {hours} 小时）',
};
const ALERT_DETAIL: Record<string, string> = {
  stalled: '进程还在，但很久没有进展。常见原因：本机睡眠、GPU 远程任务卡住、模型调用挂起。',
  stopped: '最后一条事件在 {last_event_ts}。处理完原因后可以从检查点续跑。',
  awaiting_approval: '运行停在 Step1 → Step2 的确认点，确认前不会继续。',
};

/** An alert's explanation in the interface language (the server's own text when the code is unknown). */
export function alertDetail(a: RunAlert, t: ReturnType<typeof useT>): string {
  return ALERT_DETAIL[a.code] ? t(ALERT_DETAIL[a.code], a.params) : a.detail;
}

export function AlertList({ alerts, compact = false }: { alerts: RunAlert[]; compact?: boolean }) {
  const t = useT();
  if (alerts.length === 0) return null;
  return (
    <div className="alerts">
      {alerts.map((a) => (
        <div key={a.code} className={`alert alert-${a.level}`} role={a.level === 'error' ? 'alert' : undefined}>
          <strong>{ALERT_TITLE[a.code] ? t(ALERT_TITLE[a.code], a.params) : a.title}</strong>
          {!compact && <div className="small">{ALERT_DETAIL[a.code] ? t(ALERT_DETAIL[a.code], a.params) : a.detail}</div>}
          {!compact && a.error && (
            <details className="small">
              <summary>{t('错误原文')}</summary>
              <code className="alert-error-text">{a.error}</code>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

export const STATE_LABEL: Record<RunState, string> = {
  running: '运行中',
  finished: '已结束',
  waiting_approval: '等待确认',
  stopped: '已停止',
  new: '未开始',
};

const ROUTE_LABEL: Record<string, string> = {
  active: '进化中',
  success: '达标',
  refuted: '机制被否定',
  stopped: '已停止',
  budget_exhausted: '预算用尽',
  interrupted: '已中断',
};

const KIND_LABEL: Record<string, string> = { rsi: '自主研究', refine: '方案完善', implement: '仿真验证' };

export function KindBadge({ kind }: { kind: string }) {
  const t = useT();
  return <span className="badge tone-muted">{KIND_LABEL[kind] ? t(KIND_LABEL[kind]) : kind}</span>;
}

export function StateBadge({ state }: { state: RunState }) {
  const t = useT();
  const icons: Record<RunState, string> = { running: '●', finished: '✓', waiting_approval: '◆', stopped: '△', new: '○' };
  return <span className={`badge state-${state}`}><span aria-hidden="true">{icons[state]} </span>{STATE_LABEL[state] ? t(STATE_LABEL[state]) : state}</span>;
}

const OUTCOME_LABEL: Record<string, string> = { ...ROUTE_LABEL, not_reached: '未达标', max_step: '达到步数上限' };

/** A core event line with its status words (e.g. "结束：not_reached") in the researcher's terms. */
export function eventText(message: string): string {
  return message.replace(/([：:]\s*)([a-z_]+)\b/g, (whole, sep: string, word: string) => (OUTCOME_LABEL[word] ? sep + OUTCOME_LABEL[word] : whole));
}

export function routeLabel(status: string): string {
  return ROUTE_LABEL[status] ?? status;
}

export function RouteBadge({ status }: { status: string }) {
  const t = useT();
  const tone = status === 'success' ? 'good' : status === 'active' ? 'info' : status === 'refuted' ? 'bad' : 'muted';
  return <span className={`badge tone-${tone}`}>{ROUTE_LABEL[status] ? t(ROUTE_LABEL[status]) : status}</span>;
}

export function scoreText(value: number | string | null | undefined): string {
  return typeof value === 'number' ? num(value) : value || '—';
}

export function num(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

export function signed(value: number | null | undefined, digits = 3): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '—';
}

/** Numerically named datasets in numeric order, other names after. */
export function datasetOrder(a: string, b: string): number {
  const value = (d: string) => (/^\d+[A-Za-z]?$/.test(d) ? parseInt(d, 10) : Infinity);
  return value(a) - value(b) || a.localeCompare(b);
}

/** "a / b" for the datasets a run was measured on (the project's own dataset names). */
export function datasetsLabel(datasets: string[] | undefined): string {
  return [...(datasets ?? [])].sort(datasetOrder).join(' / ') || '—';
}

/** Per-dataset values in dataset order, "0.553 / 0.555"; any number of datasets. */
export function pair(value: Pair | undefined, digits = 2, sign = false): string {
  if (!value) return '—';
  const keys = Object.keys(value).sort(datasetOrder);
  return keys.map((d) => (sign ? signed(value[d], digits) : num(value[d], digits))).join(' / ') || '—';
}

export function ErrorNote({ error }: { error: string }) {
  return <div className="note note-error">{error}</div>;
}

function storedWidth(key: string, fallback: number): number {
  try {
    const value = Number(window.localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

/**
 * A draggable column border. `side` says which neighbour the width belongs to: 'before' grows when dragged right
 * (a left column), 'after' grows when dragged left (a right column). The width is remembered per browser;
 * double-click restores the default.
 */
export function useSplit(key: string, initial: number, min: number, max: () => number, side: 'before' | 'after') {
  const [width, setWidth] = useState(() => storedWidth(key, initial));
  const [dragging, setDragging] = useState(false);
  const latest = useRef(width);
  const maxRef = useRef(max);
  maxRef.current = max;
  const endDrag = useRef<(() => void) | null>(null);

  const clamp = useCallback((value: number) => Math.round(Math.max(min, Math.min(maxRef.current(), value))), [min]);
  latest.current = clamp(width);
  const persist = (value: number) => {
    try {
      window.localStorage.setItem(key, String(value));
    } catch {
      /* not remembered */
    }
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    endDrag.current?.();
    event.preventDefault();
    const x0 = event.clientX;
    const w0 = latest.current;
    setDragging(true);
    const move = (e: PointerEvent) => setWidth(clamp(side === 'before' ? w0 + e.clientX - x0 : w0 - (e.clientX - x0)));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      setDragging(false);
      persist(clamp(latest.current));
      endDrag.current = null;
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    endDrag.current = up;
  };
  const onDoubleClick = () => { const value = clamp(initial); setWidth(value); persist(value); };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const direction = side === 'before' ? 1 : -1;
    const value = clamp(event.key === 'Home' ? initial : event.key === 'End' ? maxRef.current() : latest.current + (event.key === 'ArrowRight' ? 16 : -16) * direction);
    setWidth(value); persist(value);
  };
  useEffect(() => {
    const resize = () => setWidth((value) => clamp(value));
    window.addEventListener('resize', resize);
    return () => { window.removeEventListener('resize', resize); endDrag.current?.(); };
  }, [clamp]);
  return { width: clamp(width), dragging, handle: { onPointerDown, onDoubleClick, onKeyDown, 'aria-valuenow': clamp(width), 'aria-valuemin': min, 'aria-valuemax': Math.max(min, maxRef.current()) } };
}

export function Splitter({ dragging, handle, label }: { dragging: boolean; handle: ReturnType<typeof useSplit>['handle']; label: string }) {
  const t = useT();
  return (
    <div role="separator" tabIndex={0} aria-orientation="vertical" aria-label={t(label)} title={t('拖动调整宽度，双击恢复')}
      className={dragging ? 'splitter dragging' : 'splitter'} {...handle} />
  );
}

/** A "?" that shows its explanation on hover or focus; pages keep only titles, labels, values and actions. */
export function Hint({ text, params }: { text: string; params?: Record<string, string | number | null | undefined> }) {
  const t = useT();
  const body = t(text, params);
  return <span className="hint" tabIndex={0} role="note" aria-label={body}>?<span className="hint-body" aria-hidden="true">{body}</span></span>;
}

/** The project's dataset names in order, joined. */
export function datasetNames(datasets: string[] | undefined, separator = '、'): string {
  return [...(datasets ?? [])].sort(datasetOrder).join(separator) || '—';
}
