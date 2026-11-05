import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  api, type CodingSummary, type EventItem, type IterationDiff, type IterationRow, type Route,
  type RunDetail, type RouteNote, type Step1Round,
} from '../api';
import {
  AlertList, ErrorNote, KindBadge, RouteBadge, Splitter, StateBadge, num, pair, routeLabel, scoreText, useSplit,
  datasetsLabel, Hint, eventText } from '../components/ui';
import { useT } from '../prefs';
import { useConfirm } from '../components/Confirm';
import { RunFlow } from '../components/RsiFlow';
import EvolutionStory from '../components/EvolutionStory';
import { FollowupExperiments } from '../components/FollowupExperiments';
import { ApprovalWorkbench } from './ApprovalWorkbench';
import { readLocal, useProjects, visitKey, writeLocal, type Visit } from '../projects';
import { markSeen } from '../activity';
import { MetricTable } from '../components/Metrics';
import { checkLines, failedChecks, primaryLabel, verifyGap } from '../metrics';

type Tab = 'progress' | 'replay' | 'followups' | 'events' | 'report' | 'contract' | 'log';
const TABS: [Tab, string][] = [['progress', '进展'], ['replay', '进化回放'], ['followups', '补充实验'], ['events', '事件'], ['report', '报告'], ['contract', '研究约定'], ['log', '运行日志']];
type ViewerTab = 'eval' | 'code' | 'coding' | 'record';
const VIEWER_TABS: [ViewerTab, string][] = [['eval', '评测对比'], ['code', '代码改动'], ['coding', '编码过程'], ['record', '判断与记录']];
const VERDICT: Record<string, string> = { continue: '继续', promote: '晋级完整验收', refuted: '机制被否定', stop: '停止' };
type Focus = { route: string; iteration: number };

const reachedVerify = (it: IterationRow) => Object.keys(it.verify ?? {}).length > 0;

export function RunView({ id, sidebarWidth = 248 }: { id: string; sidebarWidth?: number }) {
  const confirm = useConfirm();
  const t = useT();
  const { current } = useProjects();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('progress');
  const [busy, setBusy] = useState(false);
  const [proposalRound, setProposalRound] = useState<string | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [step1Shown, setStep1Shown] = useState(false); // the replay is on its Step1 part: no round to show beside it
  const [viewerTab, setViewerTab] = useState<ViewerTab>('eval');
  const [stage, setStage] = useState<'verify' | 'screen' | null>(null);
  const refreshTimer = useRef<number | undefined>(undefined);
  const requestVersion = useRef(0);
  const viewer = useSplit('mars.viewer', 520, 300, () => Math.max(300, window.innerWidth - sidebarWidth - 12 - (window.innerWidth < 1100 ? 300 : 420)), 'after');

  const refresh = useCallback(() => {
    const version = ++requestVersion.current;
    api.run(id).then((r) => { if (version === requestVersion.current) { setRun(r); setError(''); } }, (e: Error) => { if (version === requestVersion.current) setError(e.message); });
  }, [id]);

  useEffect(() => {
    setRun(null);
    setEvents([]);
    setError(''); setProposalRound(null);
    const params = new URLSearchParams(window.location.hash.split('?')[1] ?? '');
    const previous = readLocal<Visit | null>(visitKey(current?.id ?? 'current'), null);
    const route = params.get('route') ?? (previous?.id === id ? previous.route : null);
    const iteration = Number(params.get('iteration') ?? (previous?.id === id ? previous.iteration : 0));
    setFocus(route && iteration > 0 ? { route, iteration } : null);
    const nextViewer = params.get('viewer') ?? (previous?.id === id ? previous.viewer : null);
    if (VIEWER_TABS.some(([key]) => key === nextViewer)) setViewerTab(nextViewer as ViewerTab);
    refresh();
    const source = new EventSource(api.streamUrl(id));
    source.onmessage = (message) => {
      let event: EventItem;
      try { event = JSON.parse(message.data) as EventItem; } catch { return; }
      setEvents((items) => (items.some((e) => e.index === event.index) ? items : [...items, event].slice(-5000)));
      window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(refresh, 800); // a burst of events → one detail reload
    };
    const poll = window.setInterval(refresh, 30000); // process state (running/stopped) has no event of its own
    return () => {
      source.close();
      window.clearInterval(poll);
      window.clearTimeout(refreshTimer.current);
      requestVersion.current++;
    };
  }, [id, refresh]);

  // Before any iteration has finished, the first route is still selectable: its notes box is wanted most right then.
  const goal = useMemo(() => (run ? run.recommended ?? (run.routes[0] ? { route: run.routes[0].id, iteration: 0 } : null) : null), [run]);
  useEffect(() => {
    if (!focus && goal) setFocus(goal);
  }, [focus, goal]);

  useEffect(() => {
    if (run?.id === id) markSeen(current?.id, id, run.events);
    if (run?.id === id) writeLocal(visitKey(current?.id ?? 'current'), {
      id, route: focus?.route, iteration: focus?.iteration, viewer: viewerTab, viewedAt: new Date().toISOString(), events: run.events,
    });
  }, [run?.id, run?.events, id, focus?.route, focus?.iteration, viewerTab, tab, current?.id]);

  const act = async (action: 'resume' | 'stop') => {
    const prompt = action === 'stop'
      ? t('停止运行 {id}？内核会结束它的训练、网关和编码进程，检查点保留，之后可以续跑。GPU 上已提交的那次训练会自己跑完。', { id })
      : t('从检查点续跑 {id}？', { id });
    if (!(await confirm(prompt))) return;
    setBusy(true);
    try {
      await (action === 'stop' ? api.stop(id) : api.resume(id));
      window.setTimeout(refresh, 1500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!run) return <div className="page">{error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取 {id}…', { id })}</p>}</div>;
  if (run.state === 'waiting_approval' || proposalRound) return <ApprovalWorkbench key={`${run.id}:${proposalRound ?? 'pending'}`} run={run} sidebarWidth={sidebarWidth} round={proposalRound ?? undefined} onDone={() => { setProposalRound(null); refresh(); }} />;

  const route = run.routes.find((r) => r.id === focus?.route) ?? null;
  const it = route?.iterations.find((x) => x.iteration === focus?.iteration) ?? null;
  const showViewer = run.kind !== 'implement' && run.routes.length > 0;
  const pick = (next: ViewerTab, nextStage?: 'verify' | 'screen') => {
    setViewerTab(next);
    if (nextStage) setStage(nextStage);
  };

  const main = (
    <div className="wb-main">
      <a className="back-link" href="#/">← {t('返回首页')}</a>
      <div className="wb-head">
        <div>
          <div className="wb-meta">
            <strong className="mono" style={{ color: 'var(--text)' }}>{run.id}</strong>
            <KindBadge kind={run.kind} /> <StateBadge state={run.state} />{!current?.connected && <span className="tag">{t('只读')}</span>}
            <span>{t('开始于 {ts}', { ts: run.started ?? '—' })} · {t('{n} 条事件', { n: run.events })}</span>
          </div>
          <h1 className="wb-title">{run.title}</h1>
        </div>
        <div className="actions">
          {(current?.connected || current?.demo) && <div className="export-group" role="group" aria-label={t('导出')}>
            <span className="muted small">{t('导出')}</span>
            <a className="button" href={api.exportUrl(id, 'docx')} download title={t('完整报告：约定、Step1 排序与交接方案、Step2 逐轮表格与判断、评测图、内核报告原文')}>Word</a>
            <a className="button" href={api.exportUrl(id, 'pptx')} download title={t('周报摘要：目标、概要、排序、路线一览、最好一轮的指标与评测图、最近判断')}>PPT</a>
            <a className="button" href={api.exportUrl(id, 'xlsx')} download title={t('逐轮数据：Step1 排序、Step2 逐轮、筛选明细、验收、事件、主指标图表')}>Excel</a>
          </div>}
          {current?.connected && run.state === 'running' && <button disabled={busy} onClick={() => act('stop')}>{t('停止')}</button>}
          {current?.connected && run.state === 'stopped' && <button className="primary" disabled={busy} onClick={() => act('resume')}>{t('从检查点续跑')}</button>}
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      <AlertList alerts={run.alerts} />
      <div className="tabs" role="tablist">
        {TABS.filter(([key]) => key !== 'followups' || Boolean(run.followups?.length)).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'tab active' : 'tab'} onClick={() => setTab(key)}>
            {t(label)}
          </button>
        ))}
      </div>
      {tab === 'progress' && (
        <Progress run={run} goal={goal} focus={focus} setFocus={setFocus} route={route} it={it} viewerTab={viewerTab} stage={stage} pick={pick} onProposal={setProposalRound} onRefresh={refresh} />
      )}
      {tab === 'replay' && <ReplayTab runId={id} focus={focus} onFocus={setFocus} onStep1={setStep1Shown} />}
      {tab === 'followups' && <FollowupExperiments experiments={run.followups ?? []} onSelect={(route, iteration) => {
        setFocus({ route, iteration }); setStage('verify'); setTab('progress');
      }} />}
      {tab === 'events' && <Events events={events} />}
      {tab === 'report' && <Report id={id} available={run.has_report} />}
      {tab === 'contract' && <pre className="code-block">{JSON.stringify(run.contract, null, 2)}</pre>}
      {tab === 'log' && <Log id={id} />}
    </div>
  );

  if (!showViewer) return main;
  return (
    <div className="workbench" style={{ gridTemplateColumns: `minmax(0, 1fr) 6px ${viewer.width}px` }}>
      {main}
      <Splitter dragging={viewer.dragging} handle={viewer.handle} label="拖动调整查看器宽度，双击恢复" />
      <section className="viewer" aria-label={t('查看器')}>
        <div className="tabs" role="tablist">
          {VIEWER_TABS.map(([key, label]) => (
            <button key={key} role="tab" aria-selected={viewerTab === key} className={viewerTab === key ? 'tab active' : 'tab'} onClick={() => setViewerTab(key)}>
              {t(label)}
            </button>
          ))}
        </div>
        <div className="viewer-body">
          {tab === 'replay' && step1Shown ? (
            <p className="viewer-empty">{t('Step1 调研阶段')}</p>
          ) : !route || !it ? (
            <p className="viewer-empty">{t(route && !route.iterations.length ? '还没有完成的轮次' : '选择一轮')}</p>
          ) : (
            <>
              <div className="muted small"><span className="mono">{route.id}</span> · {t('第 {k} 轮', { k: it.iteration })}</div>
              {viewerTab === 'eval' && <EvalView runId={run.id} route={route.id} it={it} stage={stage} setStage={setStage} metrics={run.metrics} />}
              {viewerTab === 'code' && <CodeView runId={run.id} route={route.id} it={it} />}
              {viewerTab === 'coding' && <CodingView runId={run.id} route={route.id} it={it} />}
              {viewerTab === 'record' && <RecordView runId={run.id} route={route.id} it={it} />}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

// ----- centre column -----
function Progress(props: {
  run: RunDetail; goal: Focus | null; focus: Focus | null; setFocus: (f: Focus) => void; route: Route | null; it: IterationRow | null;
  viewerTab: ViewerTab; stage: 'verify' | 'screen' | null; pick: (tab: ViewerTab, stage?: 'verify' | 'screen') => void;
  onProposal: (round: string) => void; onRefresh: () => void;
}) {
  const { run, goal, focus, setFocus, route, it } = props;
  const t = useT();
  const confirm = useConfirm();
  const { current } = useProjects();
  const [routeError, setRouteError] = useState('');
  const [stoppingRoute, setStoppingRoute] = useState(false);
  const stopRoute = async () => {
    if (!route || !(await confirm(t('停止路线 {id}？当前轮次结束后会停止，其余路线继续。', { id: route.id })))) return;
    setStoppingRoute(true); setRouteError('');
    try { await api.stopRoute(run.id, route.id); props.onRefresh(); } catch (e) { setRouteError((e as Error).message); }
    finally { setStoppingRoute(false); }
  };
  if (run.kind === 'implement') return <ImplementationCard run={run} />;
  return (
    <div className="stack">
      <RunFlow run={run} />
      {run.routes.length > 0 && <Narrative run={run} goal={goal} />}
      {run.routes.length > 0 && (
        <section className="stack" style={{ gap: 8 }}>
          <div className="section-head"><h2>{t('进化路线')}</h2></div>
          <div className="route-chips">
            {run.routes.map((r) => (
              <button key={r.id} type="button" className={r.id === focus?.route ? 'route-chip on' : 'route-chip'}
                onClick={() => setFocus({ route: r.id, iteration: r.recommended_iteration ?? r.iterations[r.iterations.length - 1]?.iteration ?? 0 })}
                title={r.title}>
                <span className="mono">{r.id}</span>
                <span className="route-name">{r.title.split(/[：:]/)[0]}</span>
                <span className="muted">{t(routeLabel(r.status))}</span>
                {r.running_iteration && <span className="muted">· {t('第 {k} 轮进行中', { k: r.running_iteration })}</span>}
              </button>
            ))}
          </div>
          {route && (
            <>
              <div className="section-head"><span className="muted small">{route.title}</span>{current?.connected && run.state === 'running' && route.status === 'active' && <button type="button" disabled={stoppingRoute} onClick={stopRoute}>{t('停止此路线')}</button>}</div>
              {routeError && <ErrorNote error={routeError} />}
              <div className="iter-strip" role="group" aria-label={t('轮次')}>
                {route.iterations.map((x) => (
                  <button key={x.iteration} type="button" onClick={() => setFocus({ route: route.id, iteration: x.iteration })}
                    className={`iter-btn${x.iteration === it?.iteration ? ' on' : ''}${reachedVerify(x) ? ' verify' : ''}`}
                    title={x.summary}>
                    {x.iteration}{x.verify_passed === false ? ' ✕' : x.screen_passed ? ' ✓' : ''}
                  </button>
                ))}
              </div>
            </>
          )}
        </section>
      )}
      {route && it && <StepLog run={run} route={route} it={it} viewerTab={props.viewerTab} stage={props.stage} pick={props.pick} />}
      {route && <RouteNotes runId={run.id} route={route.id} open={run.state !== 'finished'} iterations={`${route.iterations.length}:${route.running_iteration ?? ''}:${route.status}:${run.state}`} />}
      {run.user_proposal && (
        <div className="card">
          <details open={run.state === 'waiting_approval'}>
            <summary>{t('你提交的方案原文')}</summary>
            <pre className="code-block">{run.user_proposal}</pre>
          </details>
        </div>
      )}
      {run.kind === 'rsi' ? <section className="stack" style={{ gap: 10 }}>
        <h2>{t('提方案（Step1）')}</h2>
        {run.step1.length === 0 && <p className="muted">{t('Step1 尚未产出。')}</p>}
        {[...run.step1].reverse().map((round, i) => <div key={round.round}><Step1Card round={round} open={i === 0 && run.routes.length === 0} />{round.ranking.length > 0 && <button type="button" className="text-button" onClick={() => props.onProposal(round.round)}>{t('查看提案与并排比较')} →</button>}</div>)}
      </section> : run.step1.filter((round) => round.ranking.length > 0).slice(-1).map((round) => // refine: only the structured proposal
        <button key={round.round} type="button" className="text-button" onClick={() => props.onProposal(round.round)}>{t('查看提案与并排比较')} →</button>)}
      {run.routes.length > 0 && (
        <details className="card">
          <summary>{t('全部轮次表格')}</summary>
          {run.routes.map((r) => <RouteTable key={r.id} route={r} onPick={(k) => setFocus({ route: r.id, iteration: k })} />)}
        </details>
      )}
    </div>
  );
}

/** Plain-language state of the run, built from the records by code (never by a model). */
function Narrative({ run, goal }: { run: RunDetail; goal: Focus | null }) {
  const t = useT();
  const iterations = run.routes.reduce((n, r) => n + r.iterations.length, 0);
  const route = run.routes.find((r) => r.id === goal?.route);
  const it = route?.iterations.find((x) => x.iteration === goal?.iteration);
  const parts = [t('{r} 条路线、共 {n} 轮迭代。', { r: run.routes.length, n: iterations })];
  if (route && it) {
    parts.push(t('重点查看 {route} 第 {k} 轮的已完成评测。', { route: route.id, k: it.iteration }));
    const failed = failedChecks(it.check_gates);
    if (it.check_gates?.gates?.length) parts.push(failed.length ? t('检查门槛未通过：{f}。', { f: failed.join('、') }) : t('检查门槛全部通过。'));
    if (reachedVerify(it)) {
      parts.push(t('完整验收：门槛指标最多比基线差 {a}，{result}。', { a: pair(verifyGap(it), 4, true), result: it.verify_passed ? t('验收通过') : t('验收未通过') }));
    } else if (Object.keys(it.screen ?? {}).length) {
      parts.push(t('筛选：{m} 比基线差 {a}，{result}。', { m: primaryLabel(run.metrics), a: pair(it.screen_gap, 4, true), result: it.screen_passed ? t('通过') : t('未通过') }));
    }
  }
  return (
    <section className="stack" style={{ gap: 6 }}>
      <h2>{t('目前进展')}<Hint text="以上由程序根据运行记录生成，不经过模型改写。数值依次为 {ds}；“差于基线”为负表示比基线好。" params={{ ds: datasetsLabel(run.datasets) }} /></h2>
      <p className="narrative">{parts.join(' ')}</p>
    </section>
  );
}

type Mark = 'good' | 'bad' | 'info' | 'none';
interface Step { key: string; name: string; detail: string; meta: string; mark: Mark; tab: ViewerTab; stage?: 'verify' | 'screen'; skipped?: boolean; glyph?: string }
const ORDER = ['implementation', 'checks', 'prequick', 'screen', 'verify', 'decision'];

function steps(it: IterationRow, running: boolean, t: ReturnType<typeof useT>): Step[] {
  const stop = it.stopped_at ? ORDER.indexOf(it.stopped_at) : -1;
  const ran = (key: string) => stop < 0 || ORDER.indexOf(key) <= stop;
  const failedAt = (key: string) => it.stopped_at === key;
  const glyph: Record<Mark, string> = { good: '✓', bad: '✕', info: '→', none: '·' };
  const list: Step[] = [];
  const impl = it.implementation ?? { ok: it.implemented, turns: 0, files: [] };
  list.push({
    key: 'implementation', name: t('实现'), tab: 'code',
    detail: impl.ok === null ? t('编码 Agent 进行中') : t('编码 Agent {turns} 轮 · 改 {files} 个文件', { turns: impl.turns, files: impl.files.length }),
    meta: t('查看改动'), mark: impl.ok === false || failedAt('implementation') ? 'bad' : impl.ok ? 'good' : 'info',
  });
  const gates = it.check_gates?.gates ?? [];
  const failed = failedChecks(it.check_gates);
  list.push({
    key: 'checks', name: t('检查'), tab: 'record', skipped: !ran('checks') || gates.length === 0,
    detail: gates.length === 0 ? t('未进行') : failed.length ? t('未通过：{f}', { f: failed.join('、') }) : t('{n} 项门槛通过', { n: gates.length }),
    meta: '', mark: failedAt('checks') || failed.length ? 'bad' : gates.length ? 'good' : 'none',
  });
  const pq = it.prequick ?? { passed: null, metrics: null, gates: null, error: null };
  const pqRan = pq.passed !== null && pq.passed !== undefined;
  list.push({
    key: 'prequick', name: t('起步检查'), tab: 'record', skipped: !pqRan && !failedAt('prequick'),
    detail: !pqRan ? t('未进行') : (pq.gates ?? []).map((g) => `${g.metric} ${num(g.value, 4)}`).join(' · ') || (pq.passed ? t('通过') : t('未通过')),
    meta: '', mark: failedAt('prequick') || pq.passed === false ? 'bad' : pqRan ? 'good' : 'none',
  });
  const screened = Object.keys(it.screen ?? {}).length > 0;
  const margin = (d: string) => num(it.screen?.[d]?.margin, 4);
  list.push({
    key: 'screen', name: t('筛选'), tab: 'eval', stage: 'screen', skipped: !screened,
    detail: screened ? t('主指标差于基线 {a}', { a: pair(it.screen_gap, 4, true) }) : t('未进行'),
    meta: screened ? t('门槛 {a}', { a: Object.keys(it.screen).map(margin).join(' / ') }) : '',
    mark: !screened ? 'none' : it.screen_passed ? 'good' : 'bad',
  });
  const verified = reachedVerify(it);
  list.push({
    key: 'verify', name: t('完整验收'), tab: 'eval', stage: 'verify', skipped: !verified && !(it.screen_passed && running),
    detail: verified
      ? t('门槛指标最多差于基线 {a}', { a: pair(verifyGap(it), 4, true) })
      : it.screen_passed && running ? t('进行中') : t('未进行'),
    meta: verified ? (it.verify_passed ? t('通过') : t('未通过')) : '',
    mark: verified ? (it.verify_passed ? 'good' : 'bad') : it.screen_passed && running ? 'info' : 'none',
  });
  const verdict = it.verdict ? t(VERDICT[it.verdict] ?? it.verdict) : null;
  list.push({
    key: 'decision', name: t('决策'), tab: 'record', skipped: !verdict && !it.decision_error,
    detail: it.decision_error ? t('决策调用失败') : verdict ? (it.next_change ? `${verdict}：${it.next_change}` : verdict) : t('未进行'),
    meta: '', mark: it.decision_error || it.verdict === 'refuted' || it.verdict === 'stop' ? 'bad' : it.verdict === 'promote' ? 'good' : verdict ? 'info' : 'none',
  });
  return list.map((s) => ({ ...s, glyph: glyph[s.mark] }));
}

function StepLog({ run, route, it, viewerTab, stage, pick }: {
  run: RunDetail; route: Route; it: IterationRow; viewerTab: ViewerTab; stage: 'verify' | 'screen' | null;
  pick: (tab: ViewerTab, stage?: 'verify' | 'screen') => void;
}) {
  const t = useT();
  const running = run.state === 'running' && route.running_iteration === it.iteration;
  const list = steps(it, run.state === 'running', t);
  const base = it.parent_iteration === null ? null : it.parent_iteration === 0 ? t('从基线起步') : t('基于第 {k} 轮', { k: it.parent_iteration });
  return (
    <section className="stack" style={{ gap: 8 }}>
      <div className="section-head">
        <h2><span className="mono">{route.id}</span> {t('第 {k} 轮', { k: it.iteration })}{running && <span className="muted"> · {t('进行中')}</span>}</h2>
        <span className="muted small">{[base, it.branch].filter(Boolean).join(' · ')}</span>
      </div>
      {it.summary && <p className="prose">{it.summary}</p>}
      <div className="steps">
        {list.map((s) => {
          const on = s.tab === viewerTab && (!s.stage || s.stage === (stage ?? 'verify'));
          return (
            <button key={s.key} type="button" className={`step${on ? ' on' : ''}${s.skipped ? ' skipped' : ''}`} onClick={() => pick(s.tab, s.stage)}>
              <span className={`step-mark mark-${s.mark}`}>{s.glyph}</span>
              <span className="step-name">{s.name}</span>
              <span className="step-detail">{s.detail}</span>
              {s.meta && <span className="step-meta">{s.meta}</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function RouteTable({ route, onPick }: { route: Route; onPick: (k: number) => void }) {
  const t = useT();
  const ds = datasetsLabel([...new Set(route.iterations.flatMap((it) => it.datasets ?? []))]);
  return (
    <div style={{ marginTop: 12 }}>
      <div className="run-card-head">
        <h3><span className="mono">{route.id}</span> {route.title}</h3>
        <RouteBadge status={route.status} />
      </div>
      <p className="muted small">
        {route.running_iteration ? t('第 {k} 轮进行中', { k: route.running_iteration }) : t('共 {n} 轮', { n: route.iterations.length })}
        {route.best && ` · ${t('最好一轮筛选主指标平均差于基线 {s}（第 {k} 轮）', { s: num(route.best.score, 4), k: route.best.iteration })}`}
      </p>
      {route.iterations.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('轮')}</th><th>{t('改动')}</th><th>{t('拦截于')}</th><th>{t('检查')}</th><th>{t('起步检查')}</th>
                <th>{t('筛选主指标差于基线 {ds}', { ds })}</th><th>{t('验收')}</th><th>{t('决策')}</th>
              </tr>
            </thead>
            <tbody>
              {route.iterations.map((it) => (
                <tr key={it.iteration} className="clickable" onClick={() => onPick(it.iteration)}>
                  <td className="num">{it.iteration}</td>
                  <td className="summary-cell">{it.summary}</td>
                  <td>{it.stopped_at ? t(STAGE_LABEL[it.stopped_at] ?? it.stopped_at) : ''}</td>
                  <td>{it.check_gates?.gates?.length ? (failedChecks(it.check_gates).length ? t('未通过') : t('通过')) : '—'}</td>
                  <td>{it.prequick?.passed === true ? t('通过') : it.prequick?.passed === false ? t('未通过') : '—'}</td>
                  <td className="num">{pair(it.screen_gap, 4, true)}</td>
                  <td>{it.verify_passed === true ? t('通过') : it.verify_passed === false ? t('未通过') : '—'}</td>
                  <td>{it.decision_error ? <span className="tone-bad-text">{t('决策调用失败')}</span> : it.verdict ? t(VERDICT[it.verdict] ?? it.verdict) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = { implementation: '实现', checks: '检查', prequick: '起步检查', screen: '筛选', verify: '完整验收' };

function ImplementationCard({ run }: { run: RunDetail }) {
  const t = useT();
  const impl = run.implementation;
  const job = run.id.startsWith('verify-') ? run.id.slice('verify-'.length) : null;
  return (
    <div className="card">
      <h2>{t('仿真验证的实现')}</h2>
      {job && <p>{t('完整的对比结果见')} <a href={`#/verify/${job}`}>{t('仿真验证页面')}</a>。</p>}
      {impl ? (
        <dl className="proposal-fields">
          <dt>{t('结果')}</dt><dd>{impl.ok ? t('通过检查') : t('未通过检查')}</dd>
          <dt>{t('分支')}</dt><dd className="mono">{impl.branch ?? '—'} {impl.commit ? `@ ${impl.commit.slice(0, 10)}` : ''}</dd>
          <dt>{t('候选目录')}</dt><dd className="mono">{impl.candidate_dir ?? '—'}</dd>
          <dt>{t('改动文件')}</dt><dd className="mono">{impl.changed_files?.join('\n') || '—'}</dd>
        </dl>
      ) : (
        <p className="muted">{t('暂无实现结果')}</p>
      )}
    </div>
  );
}

const STEP1_LABEL: Record<string, string> = {
  analysis: '问题分析', sources: '检索', cards: '证据卡', proposals: '提案', critiques: '质询', debate: '答辩修订', ranking: '评审排序', handoff: '交接包',
};

function Step1Card({ round, open }: { round: Step1Round; open: boolean }) {
  const t = useT();
  return (
    <details className="card" open={open}>
      <summary>
        Step1 · {round.round} · {t('证据卡 {v}/{n} 核验通过', { v: round.cards.verified, n: round.cards.total })}
        {round.handoff.length > 0 && ` · ${t('交接 {ids}', { ids: round.handoff.join('、') })}`}
      </summary>
      <ol className="stages">
        {round.stages.map((s) => <li key={s.key} className={s.done ? 'done' : ''}>{t(STEP1_LABEL[s.key] ?? s.label)}</li>)}
      </ol>
      <p className="muted">{t('证据卡 {v}/{n} 原文引用核验通过 · 方向 {d} 个', { v: round.cards.verified, n: round.cards.total, d: round.directions.length })}</p>
      {round.ranking.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>{t('方案')}</th><th>{t('评分')}</th><th>{t('预检')}</th><th>{t('进入 Step2')}</th></tr></thead>
            <tbody>
              {round.ranking.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="mono">{r.id}</span> {r.title}
                    {r.one_sentence && <div className="muted small">{r.one_sentence}</div>}
                    {r.duplicate_of && <div className="muted small">{t('与 {id} 重复', { id: r.duplicate_of })}</div>}
                  </td>
                  <td className="num">{scoreText(r.score)}</td>
                  <td>{r.precheck.length === 0 ? <span className="tone-good-text">{t('通过')}</span> : <span className="small">{r.precheck.join('；')}</span>}</td>
                  <td>{r.handed_off ? '✓' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

// ----- viewer -----
function EvalView({ runId, route, it, stage, setStage, metrics }: {
  runId: string; route: string; it: IterationRow; stage: 'verify' | 'screen' | null; setStage: (s: 'verify' | 'screen') => void;
  metrics: RunDetail['metrics'];
}) {
  const t = useT();
  const plots = it.plots ?? {};
  // Datasets come from the recorded stages; images are the files the evaluation wrote ({stage}_{dataset}_{file}).
  const datasetsFor = (s: 'verify' | 'screen') => Object.keys((s === 'verify' ? it.verify : it.screen) ?? {});
  const stages = (['verify', 'screen'] as const).filter((s) => datasetsFor(s).length > 0);
  const [chosen, setDataset] = useState<string | null>(null);
  if (stages.length === 0) return <p className="viewer-empty">{t('这一轮没有评测结果')}</p>;
  const current = stage && stages.includes(stage) ? stage : stages[0];
  const available = datasetsFor(current);
  const dataset = chosen && available.includes(chosen) ? chosen : available[0];
  const entry = (current === 'verify' ? it.verify : it.screen)?.[dataset];
  const images = Object.keys(plots).filter((k) => k.startsWith(`${current}_${dataset}_`)).sort();
  return (
    <>
      <div className="section-head">
        <div className="seg" role="group" aria-label={t('阶段')}>
          {stages.map((s) => (
            <button key={s} type="button" className={s === current ? 'on' : ''} onClick={() => setStage(s)}>
              {s === 'verify' ? t('完整验收') : t('筛选')}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label={t('数据集')}>
          {available.map((d) => (
            <button key={d} type="button" className={d === dataset ? 'on' : ''} onClick={() => setDataset(d)}>{d}</button>
          ))}
        </div>
      </div>
      <MetricTable entry={entry} metrics={metrics} />
      {images.map((name) => (
        <div key={name} className="plots side-by-side">
          <figure className="eval-figure">
            <img src={api.runPlotUrl(runId, route, name, it.iteration)} alt={t('{route} 第 {k} 轮 {d} 候选', { route, k: it.iteration, d: dataset })} />
            <figcaption>{t('候选')} · {name.slice(current.length + dataset.length + 2)}</figcaption>
          </figure>
          <figure className="eval-figure">
            <img src={api.runPlotUrl(runId, 'baseline', `baseline_${name}`)} alt={t('{d} 基线', { d: dataset })}
              onError={(e) => ((e.currentTarget.parentElement as HTMLElement).style.display = 'none')} />
            <figcaption>{t('基线')}</figcaption>
          </figure>
        </div>
      ))}
    </>
  );
}

function useFetch<T>(load: () => Promise<T>, deps: unknown[]): { data: T | null; error: string } {
  const [state, setState] = useState<{ data: T | null; error: string }>({ data: null, error: '' });
  useEffect(() => {
    let alive = true;
    setState({ data: null, error: '' });
    load().then((data) => alive && setState({ data, error: '' }), (e: Error) => alive && setState({ data: null, error: e.message }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

function DiffBody({ patch }: { patch: string }) {
  return (
    <pre className="diff">
      {patch.split('\n').map((line, i) => {
        const cls = line.startsWith('@@') ? 'hunk' : line.startsWith('+') && !line.startsWith('+++') ? 'add' : line.startsWith('-') && !line.startsWith('---') ? 'del' : '';
        return <span key={i} className={cls}>{line || ' '}</span>;
      })}
    </pre>
  );
}

const FILE_STATUS: Record<string, string> = { added: '新增', modified: '修改', deleted: '删除' };

function CodeView({ runId, route, it }: { runId: string; route: string; it: IterationRow }) {
  const t = useT();
  const { data, error } = useFetch<IterationDiff>(() => api.diff(runId, route, it.iteration), [runId, route, it.iteration]);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="viewer-empty">{t('正在读取…')}</p>;
  const base = it.parent_iteration === null ? t('上一版本') : it.parent_iteration === 0 ? t('基线') : t('第 {k} 轮', { k: it.parent_iteration });
  return (
    <>
      <div className="muted small">
        {t('相对{base}改了 {n} 个文件', { base, n: data.files.length })} · <span className="mono">{data.base_commit?.slice(0, 8) ?? '—'} → {data.commit?.slice(0, 8) ?? '—'}</span>
      </div>
      {data.files.length === 0 && <p className="viewer-empty">{t('没有代码改动')}</p>}
      {data.files.map((f, i) => (
        <details key={f.path} className="diff-file" open={i === 0}>
          <summary>
            <span className="mono">{f.path}</span>
            <span className="badge tone-muted">{t(FILE_STATUS[f.status] ?? f.status)}</span>
            {f.truncated && <span className="badge tone-warn">{t('补丁过大，已截断')}</span>}
          </summary>
          <DiffBody patch={f.patch} />
        </details>
      ))}
    </>
  );
}

const OUTCOME: Record<string, [string, string]> = {
  ok: ['通过', 'good'], check_failed: ['检查未通过', 'bad'], smoke_failed: ['冒烟测试失败', 'bad'], error: ['出错', 'bad'],
};

function CodingView({ runId, route, it }: { runId: string; route: string; it: IterationRow }) {
  const t = useT();
  const { data, error } = useFetch<CodingSummary>(() => api.coding(runId, route, it.iteration), [runId, route, it.iteration]);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="viewer-empty">{t('正在读取…')}</p>;
  const minutes = data.started && data.ended ? Math.round((data.ended - data.started) / 60) : null;
  return (
    <>
      <dl className="kv">
        <dt>{t('用时')}</dt><dd>{minutes === null ? '—' : t('{m} 分钟', { m: minutes })}</dd>
        <dt>{t('修复回合')}</dt><dd>{data.turns.length}</dd>
        <dt>{t('工具调用')}</dt><dd>{Object.entries(data.tools).map(([k, n]) => `${k} ${n}`).join(' · ') || '—'}</dd>
        <dt>{t('工具出错')}</dt><dd>{data.tool_errors}</dd>
        <dt>{t('权限')}</dt><dd>{t('批准 {a} · 拒绝 {d}', { a: data.permissions.approved, d: data.permissions.denied })}</dd>
        <dt>{t('改过的文件')}</dt><dd className="mono">{data.files_touched.join('\n') || '—'}</dd>
      </dl>
      {data.turns.map((turn) => {
        const [label, tone] = OUTCOME[turn.outcome] ?? [turn.outcome, 'muted'];
        return (
          <div key={turn.turn} className="card" style={{ padding: 12 }}>
            <div className="run-card-head">
              <strong>{t('第 {n} 回合', { n: turn.turn + 1 })}</strong>
              <span className="chips">
                <span className={`badge tone-${tone}`}>{t(label)}</span>
                <span className="muted small">{turn.seconds ? t('{m} 分钟', { m: Math.round(turn.seconds / 60) }) : ''}</span>
              </span>
            </div>
            {turn.violations && turn.violations.length > 0 && <p className="tone-bad-text small">{t('越界：{v}', { v: turn.violations.join('；') })}</p>}
            {turn.summary && <p className="prose">{turn.summary}</p>}
          </div>
        );
      })}
    </>
  );
}

function RecordView({ runId, route, it }: { runId: string; route: string; it: IterationRow }) {
  const t = useT();
  const { data, error } = useFetch<Record<string, unknown>>(() => api.record(runId, route, it.iteration), [runId, route, it.iteration]);
  const decision = (data?.decision ?? {}) as Record<string, unknown>;
  const list = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
  return (
    <>
      <h3>{t('机制判断')}</h3>
      <p className="prose">{it.assessment || '—'}</p>
      {list(decision.evidence_for).length > 0 && (<><h3>{t('支持的证据')}</h3><ul className="prose">{list(decision.evidence_for).map((x) => <li key={x}>{x}</li>)}</ul></>)}
      {list(decision.evidence_against).length > 0 && (<><h3>{t('反对的证据')}</h3><ul className="prose">{list(decision.evidence_against).map((x) => <li key={x}>{x}</li>)}</ul></>)}
      <h3>{t('下一步')}</h3>
      <p className="prose">{it.next_change ?? '—'}</p>
      <dl className="kv">
        <dt>{t('分支')}</dt><dd className="mono">{it.branch ?? '—'}</dd>
        <dt>{t('提交')}</dt><dd className="mono">{it.commit?.slice(0, 10) ?? '—'}</dd>
        <dt>{t('起步检查')}</dt><dd>{it.prequick?.metrics ? Object.entries(it.prequick.metrics).map(([k, v]) => `${k} ${num(v, 4)}`).join(' · ') : '—'}</dd>
      </dl>
      {checkLines(it.check_gates).length > 0 && (<><h3>{t('检查门槛')}</h3>
        <ul className="prose">{checkLines(it.check_gates).map((c) => <li key={c.text} className={c.ok ? '' : 'tone-bad-text'}>{c.text}</li>)}</ul></>)}
      {error && <ErrorNote error={error} />}
      {data && (
        <details>
          <summary>{t('完整记录（实测证据与决策）')}</summary>
          <pre className="code-block">{JSON.stringify({ task: data.task, evidence: data.evidence, decision: data.decision }, null, 2)}</pre>
        </details>
      )}
    </>
  );
}

/** Notes for a route's next decision (R6); each shows whether that decision adopted it. Not a chat. */
function RouteNotes({ runId, route, open, iterations }: { runId: string; route: string; open: boolean; iterations: string }) {
  const t = useT();
  const [notes, setNotes] = useState<RouteNote[] | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    api.notes(runId, route).then((found) => alive && setNotes(Array.isArray(found) ? found : []), () => alive && setNotes([]));
    return () => { alive = false; };
  }, [runId, route, iterations]); // a finished iteration may have answered notes
  const send = async () => {
    setBusy(true); setError('');
    try { setNotes(await api.addNote(runId, route, text)); setText(''); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  if (!open && !notes?.length) return null;
  return <section className="stack" style={{ gap: 6 }}>
    <h2>{t('留言')} · <span className="mono">{route}</span><Hint text="下一轮决策会读入留言，并在记录里说明是否采纳。" /></h2>
    {notes?.map((n, i) => <div key={i} className="note-row">
      <span className="prose">{n.text}</span>
      {n.answer ? <span className={`badge tone-${n.answer.adopted ? 'good' : 'muted'}`} title={n.answer.reason ?? ''}>
        {t(n.answer.adopted ? '已采纳' : '未采纳')}{n.answer.iteration ? ` · ${t('第 {k} 轮', { k: n.answer.iteration })}` : ''}</span>
        : <span className="badge tone-info">{t('待下一轮')}</span>}
    </div>)}
    {error && <ErrorNote error={error} />}
    {open && <div className="path-bar"><input value={text} maxLength={2000} placeholder={t('给这条路线的下一轮留言')} onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => { if (e.key === 'Enter' && text.trim() && !busy) void send(); }} />
      <button type="button" disabled={busy || !text.trim()} onClick={send}>{t('发送')}</button></div>}
  </section>;
}

// ----- other tabs -----
/** Evolution story (visualization line): the Step1 round behind a route, then the route's rounds; routes switch here. */
function ReplayTab({ runId, focus, onFocus, onStep1 }: { runId: string; focus: Focus | null; onFocus: (f: Focus) => void; onStep1: (on: boolean) => void }) {
  const t = useT();
  // anchored on the round chosen in the workbench; picking another route here moves the right-hand viewer too
  const [anchor, setAnchor] = useState<Focus | null>(focus);
  const route = anchor?.route ?? null, iteration = anchor?.iteration ?? null;
  const { data, error } = useFetch(() => api.evolution(runId, route, iteration), [runId, route, iteration]);
  useEffect(() => {
    if (focus && data && (data.route !== focus.route || !data.rounds.some((r) => r.iteration === focus.iteration))) setAnchor(focus);
  }, [focus?.route, focus?.iteration, data]);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="muted">{t('正在读取…')}</p>;
  return <div className="stack">
    {data.routes.length > 1 && <div className="seg" role="group" aria-label={t('路线')} style={{ alignSelf: 'flex-start' }}>
      {data.routes.map((r) => <button key={r.route} type="button" className={r.route === data.route ? 'on' : ''}
        onClick={() => { if (r.iterations.length) { const next = { route: r.route, iteration: r.iterations[r.iterations.length - 1] }; setAnchor(next); onFocus(next); } }}>{r.route}</button>)}
    </div>}
    <EvolutionStory data={data} t={t} onStep1={onStep1} focus={focus?.route === data.route ? focus.iteration : null}
      onRound={(k) => { if (data.route && (focus?.route !== data.route || focus.iteration !== k)) onFocus({ route: data.route, iteration: k }); }} />
  </div>;
}

function Events({ events }: { events: EventItem[] }) {
  const t = useT();
  const sorted = [...events].reverse();
  return (
    <div className="card events">
      {sorted.length === 0 && <p className="muted">{t('暂无事件。')}</p>}
      {sorted.map((e) => (
        <div key={e.index} className="event-row">
          <span className="mono muted">{e.ts}</span>
          <span className="badge tone-muted">{e.stage}</span>
          <span>{eventText(e.message)}</span>
        </div>
      ))}
    </div>
  );
}

function Report({ id, available }: { id: string; available: boolean }) {
  const t = useT();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (available) api.report(id).then(setText, (e: Error) => setError(e.message));
  }, [id, available]);
  if (!available) return <p className="muted">{t('报告在运行结束时由内核生成。')}</p>;
  if (error) return <ErrorNote error={error} />;
  return <article className="card markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text ?? t('正在读取…')}</ReactMarkdown></article>;
}

function Log({ id }: { id: string }) {
  const t = useT();
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    api.log(id).then(setText, () => setText(''));
  }, [id]);
  return <pre className="code-block">{text === null ? t('正在读取…') : text || t('还没有运行日志')}</pre>;
}
