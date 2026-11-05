import { useEffect, useState } from 'react';
import { api, type Approval, type ApprovalCandidate, type RunDetail } from '../api';
import { ErrorNote, KindBadge, Splitter, StateBadge, num, scoreText, useSplit, Hint } from '../components/ui';
import { useProjects } from '../projects';
import { useT } from '../prefs';
import { useConfirm } from '../components/Confirm';
import { RunFlow } from '../components/RsiFlow';
import { Step1StoryView, type Step1Story } from '../components/EvolutionStory';

export function ApprovalWorkbench({ run, onDone, round, sidebarWidth = 248 }: { run: RunDetail; onDone: () => void; round?: string; sidebarWidth?: number }) {
  const confirm = useConfirm();
  const t = useT();
  const { current } = useProjects();
  const [approval, setApproval] = useState<Approval | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [view, setView] = useState('');
  const [tab, setTab] = useState<'detail' | 'compare'>('detail');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const viewer = useSplit('mars.approval.viewer', 520, 300, () => Math.max(300, window.innerWidth - sidebarWidth - 12 - (window.innerWidth < 1100 ? 300 : 420)), 'after');
  useEffect(() => {
    let alive = true;
    (round ? api.proposals(run.id, round) : api.approval(run.id)).then((found) => {
      if (!alive) return;
      setApproval(found); setSelected(found.default_selection ?? []); setView(found.candidates?.[0]?.id ?? '');
    }, (e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [run.id, round]);
  const storyRound = approval?.round ?? round;
  const [story, setStory] = useState<Step1Story | null>(null);
  useEffect(() => {
    if (!storyRound) return;
    let alive = true;
    api.step1Story(run.id, storyRound).then((s) => { if (alive) setStory(s); }, () => { /* the story is optional; the table below still works */ });
    return () => { alive = false; };
  }, [run.id, storyRound]);
  const candidates = approval?.candidates ?? [];
  const candidate = candidates.find((c) => c.id === view) ?? candidates[0];
  const picked = candidates.filter((c) => selected.includes(c.id));
  const budget = approval?.routes_budget ?? 0;
  const flagged = picked.filter((c) => c.precheck.length > 0 || c.duplicate_of);
  const pending = approval?.pending && !round;
  const toggle = (id: string) => setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  const submit = async () => {
    const lines = [t('确认以下方案进入 Step2：{ids}', { ids: selected.join('、') })];
    if (selected.length !== budget) lines.push(t('研究约定的路线数是 {b}，你选了 {n} 条。', { b: budget, n: selected.length }));
    if (flagged.length) lines.push(t('其中 {ids} 预检未通过或与其他方案重复。', { ids: flagged.map((c) => c.id).join('、') }));
    lines.push(t('确认后内核会开始真实的编码与训练。'));
    if (!(await confirm(lines.join('\n')))) return;
    setBusy(true); setError('');
    try { await api.approve(run.id, selected); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <div className="workbench approval-workbench" style={{ gridTemplateColumns: `minmax(0, 1fr) 6px ${viewer.width}px` }}>
    <div className="wb-main stack">
      <a className="back-link" href="#/">← {t('返回首页')}</a>
      <div className="wb-meta"><strong className="mono">{run.id}</strong><KindBadge kind={run.kind} /><StateBadge state={run.state} /></div>
      <h1 className="wb-title">{run.title}</h1>
      {error && <ErrorNote error={error} />}
      {pending && <div className="note note-warn"><strong>{t('研究暂停，等你确认进入进化的方案。')}</strong><Hint text="确认之前不会开始编码和 GPU 训练。" /></div>}
      <RunFlow run={run} />
      {round && <div className="note note-muted">{t('历史提案（只读）')}</div>}
      <section className="stack" style={{ gap: 8 }}><div className="section-head"><h2>{t('候选方案')}</h2></div>
        {!approval && !error && <p className="muted">{t('正在读取…')}</p>}
        <div className="table-wrap candidate-table"><table><thead><tr><th></th><th>{t('方案')}</th><th>{t('评分')}</th><th>{t('资源估计')}</th><th>{t('质询')}</th><th>{t('预检')}</th></tr></thead><tbody>
          {candidates.map((c) => <tr key={c.id} className={`clickable${candidate?.id === c.id ? ' selected-row' : ''}`} tabIndex={0}
            onClick={() => { setView(c.id); setTab('detail'); }} onKeyDown={(e) => { if (e.key === 'Enter') { setView(c.id); setTab('detail'); } }}>
            <td><input type="checkbox" aria-label={t('选择 {id}', { id: c.id })} checked={selected.includes(c.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggle(c.id)} /></td>
            <td><strong className="mono">{c.id}</strong><div>{c.title}</div></td><td>{scoreText(c.score)}</td>
            <td className="small">{shorten(c.resource_estimate, 60)}</td>
            <td className="small">{t('致命 {n}', { n: c.critiques.fatal })}</td><td><span className={c.precheck.length ? 'tone-bad-text' : 'tone-good-text'}>{t(c.precheck.length ? '✕ 未通过' : '✓ 通过')}</span></td>
          </tr>)}
        </tbody></table></div>
      </section>
      <div className="approval-bottom card">
        <div><strong>{t('已选 {n} 个', { n: selected.length })}：{selected.join('、') || '—'}</strong>
          <p className="small muted">{t(selected.length === budget ? '与研究约定的路线数一致' : '研究约定的路线数是 {b}，你选了 {n} 条。', { b: budget, n: selected.length })}</p>
          {flagged.length > 0 && <p className="small tone-bad-text">{t('其中 {ids} 预检未通过或与其他方案重复。', { ids: flagged.map((c) => c.id).join('、') })}</p>}</div>
        <div className="actions"><a className="button" href="#/">{t('稍后决定')}</a>{pending && <button type="button" className="primary" disabled={busy || !selected.length || !current?.connected} onClick={submit}>{busy ? t('正在提交…') : t('确认并开始进化')}</button>}</div>
        {!current?.connected && pending && <span className="small muted">{t('此项目尚未连接研究执行环境。')}</span>}
      </div>
      {/* the page is for choosing: how the candidates came about stays folded below the decision */}
      {story && <details className="card"><summary>{t('调研过程')} · <span className="mono">{storyRound}</span></summary><Step1StoryView story={story} t={t} /></details>}
      {round && <button type="button" onClick={onDone}>{t('返回运行进展')}</button>}
    </div>
    <Splitter dragging={viewer.dragging} handle={viewer.handle} label="拖动调整查看器宽度，双击恢复" />
    <aside className="viewer" aria-label={t('提案查看器')}><div className="tabs" role="tablist">
      <button type="button" role="tab" aria-selected={tab === 'detail'} className={`tab${tab === 'detail' ? ' active' : ''}`} onClick={() => setTab('detail')}>{t('方案详情')}</button>
      <button type="button" role="tab" aria-selected={tab === 'compare'} className={`tab${tab === 'compare' ? ' active' : ''}`} onClick={() => setTab('compare')}>{t('并排比较')} ({picked.length})</button>
    </div><div className="viewer-body">{tab === 'detail' && candidate && <CandidateDetail candidate={candidate} />}
      {tab === 'compare' && (picked.length ? <div className="table-wrap compare-table"><table><thead><tr><th>{t('比较项')}</th>{picked.map((c) => <th key={c.id}>{c.id}<div>{c.title}</div></th>)}</tr></thead><tbody>
        {['评分', '资源估计', '质询', '预检', '机制', '否定条件'].map((label) => <tr key={label}><th>{t(label)}</th>{picked.map((c) => <td key={c.id}>{label === '评分' ? scoreText(c.score) : label === '资源估计' ? c.resource_estimate : label === '质询' ? t('致命 {f} · 重要 {m}', { f: c.critiques.fatal, m: c.critiques.major }) : label === '预检' ? c.precheck.join('；') || t('通过') : label === '机制' ? c.mechanism_hypothesis || c.one_sentence : c.falsification}</td>)}</tr>)}
      </tbody></table></div> : <p className="viewer-empty">{t('先在中间勾选需要比较的方案。')}</p>)}
    </div></aside>
  </div>;
}

const shorten = (text: string, n: number) => (text && text.length > n ? `${text.slice(0, n - 1)}…` : text || '—');

function CandidateDetail({ candidate: c }: { candidate: ApprovalCandidate }) {
  const t = useT();
  return <div className="stack"><div><span className="mono muted">{c.id}</span><h2>{c.title}</h2><p className="prose">{c.one_sentence}</p></div>
    <dl className="kv"><dt>{t('评分')}</dt><dd>{scoreText(c.score)} ({c.judge_scores.map((s) => num(s)).join(' / ') || '—'})</dd>
      <dt>{t('资源估计')}</dt><dd>{c.resource_estimate || '—'}</dd>
      <dt>{t('质询')}</dt><dd>{t('致命 {f} · 重要 {m}', { f: c.critiques.fatal, m: c.critiques.major })}</dd>
      <dt>{t('预检')}</dt><dd className={c.precheck.length ? 'tone-bad-text' : 'tone-good-text'}>{c.precheck.join('；') || t('通过')}{c.duplicate_of && ` · ${t('与 {id} 重复', { id: c.duplicate_of })}`}</dd></dl>
    {([['机制假设', c.mechanism_hypothesis], ['改动的基线部分', c.removed_or_shrunk], ['新增能力', c.added_capability], ['约束论证', c.constraints_argument], ['与历史区别', c.history_relation], ['否定条件', c.falsification]]).map(([label, value]) => value && <section key={label}><h3>{t(label)}</h3><p className="prose">{value}</p></section>)}
    {([['证据卡片', c.evidence_cards], ['质询原文', c.critique_details], ['答辩原文', c.rebuttal_details], ['提案完整记录', c.proposal]] as const).map(([label, value]) => value && <details key={label}><summary>{t(label)}</summary><pre className="code-block">{JSON.stringify(value, null, 2)}</pre></details>)}
  </div>;
}
