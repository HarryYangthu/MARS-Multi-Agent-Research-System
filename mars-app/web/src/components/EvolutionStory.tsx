import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import './EvolutionStory.css';

// ----- data (server/mars_app/evolution.py) -----
interface Issue { severity: string; criterion: string; text: string }
interface Proposal {
  id: string; title: string; original_title: string; score: number | null; judge_scores: number[]; precheck: string[]; approved: boolean;
  peer: string | null; critiques: { role: string; verdict: string; issues: Issue[] }[]; rebuttals: { kind: string; issue: string; response: string }[];
}
export interface Step1Story {
  round: string; focus: string | null; focus_rq: string | null;
  directions: { id: string; name: string }[];
  questions: { id: string; question: string; paper_queries: string[]; web_queries: string[] }[];
  engines: string[]; queries: string[]; hits: Record<string, (number | null)[]>; web_queries: { query: string; hits: number }[];
  requests: number; hits_total: number; sources: number; example_query: string | null;
  lanes: { engine: string; hits: number | null; titles: string[] }[]; budget: number | null;
  picks: { index: number; source_id: string; question_id: string }[];
  cards: { id: string; title: string; question_id: string; full_text: boolean; verified: boolean }[];
  proposals: Proposal[]; approved: string[];
}
interface Round {
  iteration: number; summary: string; assessment: string; decision: string | null; stopped_at: string | null;
  modules: Record<string, string[]>; prequick_passed: boolean | null;
  screen_gap: Record<string, number | null>; verify_gap: Record<string, number | null>;
  checks: { all?: boolean | null; gates?: { check: string; field: string; ok: boolean; values: unknown[] }[] };
  tensors: { name: string; shape: number[]; rank: number | null }[];
  ladder: { implemented: boolean | null; prequick: boolean | null; screen: Record<string, boolean>; verify: Record<string, boolean> };
}
export interface EvolutionData {
  run_id: string; route: string | null; iteration: number | null; routes: { route: string; iterations: number[] }[];
  gates?: { metric: string; max_worse?: number }[]; check_gates?: { check: string; field: string }[];
  step1: Step1Story | null; rounds: Round[];
  researcher: { ts: string; message: string; approve: string[]; kind: string }[];
}

type T = (zh: string, params?: Record<string, string | number>) => string;
const same: T = (zh) => zh;

// ----- timeline -----
const S1 = ['queries', 'engines', 'merge', 'screen', 'cards', 'critique', 'judge'] as const;
type S1Stage = typeof S1[number];
const S1_MS: Record<S1Stage, number> = { queries: 4500, engines: 5000, merge: 4500, screen: 5000, cards: 4500, critique: 8000, judge: 6000 };
const THINK = 4000, CHANGE = 3200, TRAIN = 3600, JUDGE = 4500;
interface Seg { kind: 'step1'; stage: S1Stage; start: number; len: number }
interface RSeg { kind: 'round'; index: number; start: number; len: number; edit: number }
type Segment = Seg | RSeg;

const S1_NAME: Record<S1Stage, string> = { queries: '查询', engines: '检索', merge: '合并', screen: '筛选', cards: '精读核验', critique: '质询', judge: '评分批准' };
const VERDICT: Record<string, string> = { continue: '继续', refuted: '否定', promote: '晋级', stop: '停止' };
const RQ_COLORS = ['#418DD4', '#7B5CD6', '#CB7E47', '#5FAD72', '#B42318', '#8A5300', '#2E8B8B', '#9A4F0C'];

function reducedMotion() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
}
const signed = (x: number | null | undefined) => (x == null ? '—' : `${x > 0 ? '+' : x < 0 ? '−' : '±'}${Math.abs(x).toFixed(2)}`);
const typed = (text: string, ms: number, total: number) => text.slice(0, Math.ceil(Math.max(0, Math.min(1, ms / total)) * text.length));

function segments(data: EvolutionData): Segment[] {
  const out: Segment[] = [];
  let t = 0;
  if (data.step1) for (const stage of S1) { out.push({ kind: 'step1', stage, start: t, len: S1_MS[stage] }); t += S1_MS[stage]; }
  data.rounds.forEach((r, index) => {
    const changes = Object.values(r.modules).reduce((n, l) => n + (l?.length ?? 0), 0);
    const edit = Math.max(4000, changes * CHANGE);
    const len = THINK + edit + TRAIN + JUDGE;
    out.push({ kind: 'round', index, start: t, len, edit }); t += len;
  });
  return out;
}

type Link = { onStep1?: (on: boolean) => void; focus?: number | null; onRound?: (iteration: number) => void };

/**
 * Linked with the page beside it: onStep1 says whether the Step1 part is on screen, onRound reports the round being
 * shown (picked or played), and a `focus` round chosen elsewhere moves the story to that round.
 */
export default function EvolutionStory({ data, t = same, ...link }: { data: EvolutionData | null; t?: T } & Link) {
  if (!data || (!data.step1 && data.rounds.length === 0)) return <p className="es-empty">{t('暂无进化记录')}</p>;
  return <Story key={`${data.run_id}:${data.route}:${data.iteration}`} data={data} t={t} {...link} />;
}

function Story({ data, t, onStep1, focus, onRound }: { data: EvolutionData; t: T } & Link) {
  const segs = useMemo(() => segments(data), [data]);
  // the modules the run's proposals named, in order of first appearance
  const modules = useMemo(() => [...new Set(data.rounds.flatMap((r) => Object.keys(r.modules)))], [data]);
  const end = segs.length ? segs[segs.length - 1].start + segs[segs.length - 1].len : 0;
  const [time, setTime] = useState(end);
  const [playing, setPlaying] = useState(false);
  const last = useRef(0);
  useEffect(() => {
    if (!playing) return;
    if (reducedMotion()) { setTime(end); setPlaying(false); return; }
    last.current = performance.now();
    const id = window.setInterval(() => {
      const now = performance.now(), dt = Math.min(1500, now - last.current);
      last.current = now;
      setTime((x) => { const next = x + dt; if (next >= end) { setPlaying(false); return end; } return next; });
    }, 50);
    return () => window.clearInterval(id);
  }, [playing, end]);
  const done = time >= end;
  const seg = done ? segs[segs.length - 1] : segs.find((s) => time >= s.start && time < s.start + s.len) ?? segs[0];
  const local = done ? seg.len : time - seg.start;
  const roundIndex = seg.kind === 'round' ? seg.index : -1;
  const round = roundIndex >= 0 ? data.rounds[roundIndex] : null;
  const prev = roundIndex > 0 ? data.rounds[roundIndex - 1] : null;
  const inStep1 = seg.kind === 'step1';
  useEffect(() => { onStep1?.(inStep1); }, [inStep1, onStep1]);
  useEffect(() => () => onStep1?.(false), [onStep1]);
  const shown = useRef<number | null>(null);
  shown.current = round?.iteration ?? null;
  const report = useRef(onRound);
  report.current = onRound;
  useEffect(() => { if (round) report.current?.(round.iteration); }, [round?.iteration]);  // only when the round changes
  useEffect(() => {  // a round picked in the workbench: show it complete
    const target = segs.find((s) => s.kind === 'round' && data.rounds[s.index].iteration === focus);
    if (target && focus !== shown.current) { setPlaying(false); setTime(target.start + target.len - 1); }
  }, [focus]);

  const toggle = () => {
    if (playing) { setPlaying(false); return; }
    if (done) setTime(0);
    setPlaying(true);
  };
  const jump = (start: number) => { setPlaying(false); setTime(start); };

  // check gates: the round's own once its edit has begun, else the previous state
  const editStarted = seg.kind === 'round' && local >= THINK;
  const gateRound = round ? (editStarted ? round : prev) : (data.rounds.length && done ? data.rounds[data.rounds.length - 1] : null);
  const gates = gateRound?.checks?.gates ?? [];

  const chips: { label: string; start: number; len: number }[] = [];
  if (data.step1) chips.push({ label: `Step1 ${data.step1.round}`, start: 0, len: segs.filter((s) => s.kind === 'step1').reduce((n, s) => n + s.len, 0) });
  segs.forEach((s) => { if (s.kind === 'round') chips.push({ label: `${data.route} i${data.rounds[s.index].iteration}`, start: s.start, len: s.len }); });

  return (
    <section className="es">
      <header className="es-head">
        <nav className="es-chips" aria-label={t('进度')}>
          {chips.map((c) => {
            const f = Math.max(0, Math.min(1, (time - c.start) / c.len));
            const on = time >= c.start && (time < c.start + c.len || (done && c === chips[chips.length - 1]));
            return <button key={c.label} type="button" className={`es-chip${on ? ' is-on' : ''}`} onClick={() => jump(c.start)}>
              <span className="es-chip-fill" style={{ width: `${f * 100}%` }} /><span className="es-chip-label mono">{c.label}</span>
            </button>;
          })}
        </nav>
        <button type="button" className="es-play" onClick={toggle}>{t(playing ? '暂停' : done ? '▶ 播放' : '▶ 继续')}</button>
      </header>

      {data.rounds.length > 0 && <><div className="es-params">
        {gates.length ? gates.map((g) => <span key={`${g.check}.${g.field}`} className={`mono ${g.ok ? 'es-good' : 'es-muted'}`}>
          {g.ok ? '✓' : '✗'} {g.check}.{g.field} {g.values.map((v) => (typeof v === 'number' ? +v.toPrecision(3) : String(v))).join('/')}</span>)
          : <span className="es-muted">{t('检查门槛')} —</span>}
      </div>

      <div className="es-mods">
        {modules.map((key) => {
          const lit = round && editStarted && round.modules[key];
          return <span key={key} className={`es-mod${lit ? ' is-on' : ''}`}>{key}</span>;
        })}
      </div></>}

      {seg.kind === 'step1' && data.step1 && <Step1View story={data.step1} stage={seg.stage} ms={local} researcher={data.researcher} t={t} />}
      {seg.kind === 'round' && round && <RoundView round={round} prev={prev} ms={local} edit={seg.edit} route={data.route ?? ''} t={t} />}
    </section>
  );
}

// ----- Step1 -----
function Step1View({ story, stage, ms, researcher, t }: { story: Step1Story; stage: S1Stage; ms: number; researcher: EvolutionData['researcher']; t: T }) {
  const rqIndex = (id: string) => Math.max(0, story.questions.findIndex((q) => q.id === id));
  const at = (x: number) => ms >= x;
  return (
    <div className="es-stage">
      <div className="es-steps">
        {S1.map((s, i) => <span key={s} className={`es-step${s === stage ? ' is-on' : S1.indexOf(stage) > i ? ' is-past' : ''}`}>{i + 1} {t(S1_NAME[s])}</span>)}
      </div>

      {stage === 'queries' && (() => {
        const q = story.questions.find((x) => x.id === story.focus_rq) ?? story.questions[0];
        const paper = story.questions.reduce((n, x) => n + x.paper_queries.length, 0), web = story.questions.reduce((n, x) => n + x.web_queries.length, 0);
        return <>
          <div className="es-row">
            {q && <div className="es-box es-focus"><span className="es-k mono">{q.id}</span><strong>{q.question}</strong></div>}
            <span className="es-arrow">→</span>
            <div className="es-col">{q?.paper_queries.map((x, i) => <span key={x} className="es-query mono">{typed(x, ms - 800 - i * 700, 600)}</span>)}</div>
          </div>
          <div className="es-row">
            {[[String(story.questions.length), '调研问题'], [`${paper} + ${web}`, '论文 + 网页查询'], [`= ${paper + web}`, '条查询']].map(([v, k], i) =>
              at(2400 + i * 400) && <div key={k} className={`es-count${i === 2 ? ' is-key' : ''}`}><span className="mono">{v}</span><span>{t(k)}</span></div>)}
          </div>
        </>;
      })()}

      {stage === 'engines' && <>
        <span className="es-query mono es-self-start">{story.example_query}</span>
        <div className="es-lanes">
          {story.lanes.map((l, i) => at(300 + i * 500) && <div key={l.engine} className="es-box">
            <span className="es-lane-head"><strong>{l.engine}</strong><span className="mono">{at(800 + i * 500) ? l.hits ?? '×' : '…'}</span></span>
            {l.titles.map((x, k) => at(1100 + i * 500 + k * 300) && <span key={x} className="es-title">{x}</span>)}
          </div>)}
        </div>
      </>}

      {stage === 'merge' && <>
        <div className="es-grid" style={{ gridTemplateColumns: `72px repeat(${story.queries.length}, minmax(0, 1fr))` }}>
          {Object.entries(story.hits).map(([eng, row]) => <Fragment key={eng}>
            <span className="es-grid-name">{eng}</span>
            {row.map((h, q) => {
              const on = at(200 + q * 110);
              return <span key={q} className={`es-cell mono${on && h === 0 ? ' is-zero' : ''}`} style={on && h ? { background: `color-mix(in srgb, var(--accent) ${15 + h * 12}%, transparent)` } : undefined}>{on ? h ?? '×' : ''}</span>;
            })}
          </Fragment>)}
        </div>
        <div className="es-row">
          {[[story.requests, '次请求'], [story.hits_total, '条命中'], [story.sources, '个来源']].map(([v, k], i) =>
            at(2400 + i * 450) && <div key={String(k)} className={`es-count${i === 2 ? ' is-key' : ''}`}><span className="mono">{v}</span><span>{t(String(k))}</span></div>)}
        </div>
      </>}

      {stage === 'screen' && (() => {
        const budget = story.budget ?? story.picks.length;
        const cut = at(3300);
        return <div className="es-screen">
          <div className="es-tiles">
            {Array.from({ length: story.sources }, (_, i) => {
              const k = story.picks.findIndex((p) => p.index === i);
              const lit = k >= 0 && at(900 + k * 140);
              const dropped = lit && k >= budget && cut;
              return <span key={i} className={`es-tile${dropped ? ' is-cut' : ''}`} style={lit && !dropped ? { background: RQ_COLORS[rqIndex(story.picks[k].question_id) % RQ_COLORS.length] } : undefined}>{lit ? k + 1 : ''}</span>;
            })}
          </div>
          <div className="es-col">
            {cut && story.picks.length > budget && <span className="es-cutline mono">selected[:{budget}]</span>}
            {story.questions.map((q, i) => {
              const picked = story.picks.filter((p) => p.question_id === q.id).length;
              const kept = story.picks.slice(0, budget).filter((p) => p.question_id === q.id).length;
              return <span key={q.id} className={`es-rq${cut && picked && !kept ? ' is-bad' : ''}`}>
                <span className="es-swatch" style={{ background: RQ_COLORS[i % RQ_COLORS.length] }} /><span className="mono">{q.id}</span>
                <span className="mono">{at(2800) ? (cut ? `${picked} → ${kept}` : String(picked)) : ''}</span>
              </span>;
            })}
          </div>
        </div>;
      })()}

      {stage === 'cards' && <div className="es-col">
        {story.cards.map((c, i) => {
          const checked = at(300 + i * 300 + 1300);
          return at(300 + i * 300) && <div key={c.id} className={`es-card${checked && !c.verified ? ' is-bad' : ''}`}>
            <span className="mono es-accent">{c.id}</span><span className="es-ellipsis">{c.title}</span>
            <span className={c.full_text ? 'es-info' : 'es-warn'}>{t(c.full_text ? '正文' : '摘要')}</span>
            <span className={c.verified ? 'es-good' : 'es-bad'}>{checked ? (c.verified ? '✓' : '✗') : ''}</span>
          </div>;
        })}
      </div>}

      {stage === 'critique' && (() => {
        const p = story.proposals.find((x) => x.id === story.focus);
        if (!p) return null;
        const ORDER: Record<string, number> = { fatal: 0, major: 1, minor: 2 };
        return <>
          <span className="es-self-start es-focus-chip mono">{p.id} · {p.original_title || p.title}</span>
          <div className="es-lanes es-two">
            {p.critiques.map((c, li) => {
              const peer = c.role === 'peer';
              const issues = [...c.issues].sort((a, b) => (ORDER[a.severity] ?? 3) - (ORDER[b.severity] ?? 3)).slice(0, 3);
              return <div key={c.role} className={`es-box${peer ? ' es-peer' : ''}`}>
                <div className="es-inputs">
                  <strong>{t(peer ? '同行' : '审稿人')}</strong>
                  <span className="es-in">{t('约定')}</span><span className="es-in">{t('基线代码')}</span>
                  <span className="es-in es-researcher-in">{t('研究员 · 原则 · 已否决方向')}</span><span className="es-in mono">{p.id}</span>
                  {peer && p.peer && at(1000) && <span className="es-in es-extra mono">{p.peer}</span>}
                </div>
                {issues.map((x, k) => at(2200 + (k * 2 + li) * 700) && <div key={k} className="es-issue">
                  <span className={`es-sev es-sev-${x.severity}`}>{t({ fatal: '致命', major: '重要', minor: '次要' }[x.severity] ?? x.severity)}</span>
                  <span>{x.text}</span>
                </div>)}
                {at(6600) && <span className="es-counts">{(['fatal', 'major', 'minor'] as const).map((s) => `${t({ fatal: '致命', major: '重要', minor: '次要' }[s])} ${c.issues.filter((x) => x.severity === s).length}`).join(' · ')}</span>}
              </div>;
            })}
          </div>
          {at(7000) && <div className="es-row es-rebuttals">
            {(['accept', 'partial', 'rebut'] as const).map((k) => <span key={k} className={`es-reb es-reb-${k}`}>{t({ accept: '接受', partial: '部分接受', rebut: '反驳' }[k])} {p.rebuttals.filter((r) => r.kind === k).length}</span>)}
          </div>}
        </>;
      })()}

      {stage === 'judge' && (() => {
        const ranked = [...story.proposals].sort((a, b) => (a.precheck.length > 0 ? 1 : 0) - (b.precheck.length > 0 ? 1 : 0) || (b.score ?? 0) - (a.score ?? 0));
        const approval = researcher.find((e) => e.kind === 'approval' && story.focus && e.approve.includes(story.focus));
        const approvedAt = 3600;
        return <div className="es-judge">
          <div className="es-col">
            {ranked.map((p, i) => {
              const f = Math.max(0, Math.min(1, (ms - 400 - i * 250) / 1400));
              const ok = p.approved && at(approvedAt);
              return <div key={p.id} className={`es-prop${ok ? ' is-ok' : ''}${p.id === story.focus && ok ? ' is-focus' : ''}`}>
                <span className="mono">{p.id}</span><span className="es-ellipsis">{p.title}</span>
                <span className="es-scorebar"><span style={{ width: `${Math.max(0, ((p.score ?? 5) - 5) / 4) * 100 * f}%` }} /></span>
                <span className="mono">{f > 0.98 && p.score != null ? p.score.toFixed(2) : ''}</span>
                <span className="es-tag">{p.precheck.length && at(2400) ? t('预检未过') : ok ? t('批准') : ''}</span>
              </div>;
            })}
          </div>
          {at(approvedAt) && (approval || story.approved.length > 0) && <div className="es-researcher">
            <span className="es-researcher-badge">{t('研究员')}</span>
            <span>{approval ? approval.message : t('批准 {ids}', { ids: story.approved.join('、') })}</span>
          </div>}
        </div>;
      })()}
    </div>
  );
}

// ----- one Step2 round -----
function RoundView({ round, prev, ms, edit, route, t }: { round: Round; prev: Round | null; ms: number; edit: number; route: string; t: T }) {
  const phase = ms < THINK ? 'think' : ms < THINK + edit ? 'edit' : ms < THINK + edit + TRAIN ? 'train' : 'judge';
  const lines = Object.entries(round.modules).flatMap(([label, texts]) => texts.map((text) => ({ label, text })));
  const per = edit / Math.max(1, lines.length);
  const shownLines = phase === 'think' ? 0 : phase === 'edit' ? Math.min(lines.length, Math.floor((ms - THINK) / per) + 1) : lines.length;
  const tensors = phase === 'think' && prev ? prev.tensors : round.tensors;
  const before = new Map((prev?.tensors ?? []).map((x) => [x.name, x.shape.join('×')]));
  const train0 = THINK + edit;
  const ladder = [
    ['实现', round.ladder.implemented == null ? '—' : round.ladder.implemented ? '✓' : '✗', round.ladder.implemented],
    ['起步检查', round.prequick_passed == null ? '—' : round.prequick_passed ? '✓' : '✗', round.ladder.prequick],
    ['筛选', Object.keys(round.screen_gap).sort().map((d) => `${d} ${signed(round.screen_gap[d])}`).join(' · ') || '—', Object.values(round.ladder.screen).length ? Object.values(round.ladder.screen).every(Boolean) : null],
    ['完整验收', Object.keys(round.verify_gap).sort().map((d) => `${d} ${signed(round.verify_gap[d])}`).join(' · ') || '—', Object.values(round.ladder.verify).length ? Object.values(round.ladder.verify).every(Boolean) : null],
  ] as const;
  return (
    <div className="es-stage es-round">
      <div className="es-steps">
        {['提出', '修改', '训练评测', '判断'].map((s, i) => <span key={s} className={`es-step${['think', 'edit', 'train', 'judge'][i] === phase ? ' is-on' : ['think', 'edit', 'train', 'judge'].indexOf(phase) > i ? ' is-past' : ''}`}>{t(s)}</span>)}
        <span className="es-round-name mono">{route} i{round.iteration}</span>
      </div>
      <div className="es-split">
        <div className="es-col">
          <p className="es-text">{phase === 'think' ? typed(round.summary, ms - 200, THINK - 600) : round.summary}</p>
          <div className="es-diff">
            {lines.slice(0, shownLines).map((l, i) => <span key={i} className="es-diffline"><span className="es-modtag">{l.label}</span><span className="mono">{l.text}</span></span>)}
          </div>
          {phase !== 'think' && phase !== 'edit' && <div className="es-ladder">
            {ladder.map(([name, value, ok], i) => {
              const on = ms >= train0 + i * 700;
              return <div key={name} className={`es-rung${on ? (ok === true ? ' is-good' : ok === false ? ' is-bad' : '') : ''}`}><span>{t(name)}</span><span className="mono">{on ? value : '…'}</span></div>;
            })}
          </div>}
          {phase === 'judge' && <p className="es-text es-judgement">
            {round.decision && <span className="es-verdict">{t(VERDICT[round.decision] ?? round.decision)}</span>}
            {typed(round.assessment, ms - THINK - edit - TRAIN, 2400)}
          </p>}
        </div>
        <Tensors tensors={tensors} before={phase === 'think' ? new Map() : before} />
      </div>
    </div>
  );
}

/** The model's trained parameter tensors (from an auto diagnostic's tensors), grouped by layer; changed shapes light up. */
function Tensors({ tensors, before }: { tensors: Round['tensors']; before: Map<string, string> }) {
  if (!tensors.length) return null;
  const groups = new Map<string, Round['tensors']>();
  tensors.forEach((x) => { const g = x.name.split('.')[0]; groups.set(g, [...(groups.get(g) ?? []), x]); });
  const order = [...groups.keys()].sort((a, b) => (parseInt(a.replace(/\D/g, '') || '99', 10) - parseInt(b.replace(/\D/g, '') || '99', 10)) || a.localeCompare(b));
  return <div className="es-tensors">
    {order.map((g) => <div key={g} className="es-layer">
      <span className="es-layer-name mono">{g}</span>
      {groups.get(g)!.map((x) => {
        const [rows, cols] = [x.shape[0] ?? 1, x.shape[1] ?? 1];
        const w = Math.max(8, Math.min(64, Math.sqrt(cols) * 8)), h = Math.max(8, Math.min(64, Math.sqrt(rows) * 8));
        const changed = before.size > 0 && before.get(x.name) !== x.shape.join('×');
        const full = Math.min(rows, cols);
        return <div key={x.name} className={`es-tensor${changed ? ' is-changed' : ''}`}>
          <span className="es-mat" style={{ width: w, height: h }} />
          <span className="mono">{x.name.split('.').slice(1).join('.') || x.name}</span>
          <span className="mono es-muted">{x.shape.filter((n) => n !== 1 || x.shape.length === 1).join('×')}</span>
          {x.rank != null && <span className="es-rank" title={`${x.rank.toFixed(2)} / ${full}`}><span style={{ width: `${Math.min(100, (x.rank / full) * 100)}%` }} /></span>}
        </div>;
      })}
    </div>)}
  </div>;
}


/** Step1 alone (the approval page): the same research story without Step2 rounds. */
export function Step1StoryView({ story, t = same }: { story: Step1Story | null; t?: T }) {
  if (!story) return null;
  return <EvolutionStory t={t} data={{ run_id: story.round, route: null, iteration: null, routes: [], step1: story, rounds: [], researcher: [] }} />;
}
