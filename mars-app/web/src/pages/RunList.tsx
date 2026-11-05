import { useEffect, useState } from 'react';
import { api, type ProjectOverview, type RunSummary } from '../api';
import { needsAttention, useInbox } from '../activity';
import { AlertList, ErrorNote, KindBadge, RouteBadge, StateBadge, alertDetail, Hint, eventText } from '../components/ui';
import { readLocal, useProjects, visitKey, writeLocal, type Visit } from '../projects';
import { useT } from '../prefs';
import { MODEL_LABEL, useModelStatus, type ModelState } from '../modelStatus';

/** Project home: attention and changes first, with real artifact counts beside the research activity. */
export function RunList({ runs, error }: { runs: RunSummary[] | null; error: string }) {
  const t = useT();
  const { current } = useProjects();
  const { items, dismiss } = useInbox(runs);
  const [filter, setFilter] = useState('all');
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [overviewError, setOverviewError] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    let alive = true;
    api.overview().then((found) => { if (alive) setOverview(found); }, (e: Error) => { if (alive) setOverviewError(e.message); });
    return () => { alive = false; };
  }, []);
  const visit = readLocal<Visit | null>(visitKey(current?.id ?? 'current'), null);
  const previous = runs?.find((run) => run.id === visit?.id);
  const active = runs?.filter((r) => r.state === 'running') ?? [];
  const ended = runs?.filter((r) => r.state !== 'running') ?? [];
  const shown = items.filter((item) => filter === 'all' || item.kind === filter);
  const contract = overview?.contract ?? {};
  const models = useModelStatus();
  const fresh = runs !== null && runs.length === 0;
  const demo = Boolean(current?.demo); // read-only: no starts, nothing to handle
  return <section>
    <div className="page-head"><div><h1>{current?.name}{demo && <span className="tag">{t('只读')}</span>}</h1><p className="mono muted small project-path">{current?.path}</p></div>{!demo && <a className="button primary" href="#/new">+ {t('新建运行')}</a>}</div>
    {!demo && !current?.connected && <div className="note note-muted">{t('此项目尚未连接研究执行环境。')} <a href="#/project/settings">{t('项目设置')} →</a></div>}
    {error && <ErrorNote error={error} />}
    {runs === null && !error && <p className="muted">{t('正在读取…')}</p>}
    {runs !== null && !demo && <GettingStarted models={models.state} builtin={Boolean(current?.builtin)} hasRuns={!fresh} />}
    <div className="home-layout">
      <div className="stack">
        {previous && visit && <div className="resume-card card"><div><span className="small muted">{t('上次看到这里')} · {new Date(visit.viewedAt).toLocaleString()}</span>
          <strong className="mono">{previous.id}{visit.route ? ` · ${visit.route} · ${t('第 {k} 轮', { k: visit.iteration })}` : ''}</strong>
          <span className="small muted">{previous.events > visit.events ? t('之后新增 {n} 条事件', { n: previous.events - visit.events }) : t('暂时没有新的事件')}</span></div>
          <a className="button" href={`#/runs/${encodeURIComponent(previous.id)}${visit.route ? `?route=${encodeURIComponent(visit.route)}&iteration=${visit.iteration}&viewer=${visit.viewer ?? 'eval'}` : ''}`}>{t('继续')} →</a></div>}
        {!fresh && !demo && <section className="stack" style={{ gap: 10 }}>
          <div className="section-head"><h2>{t('待处理')} <span className="muted">· {items.length}</span></h2>
            <div className="pills" role="group" aria-label={t('收件箱筛选')}>{[['all', '全部'], ['approval', '待确认'], ['result', '新结果'], ['error', '异常']].map(([id, name]) => <button type="button" key={id} aria-pressed={filter === id} className={filter === id ? 'on' : ''} onClick={() => setFilter(id)}>{t(name)}</button>)}</div></div>
          <div className="inbox">{shown.map((item) => <div key={item.key} className="inbox-row">
            <span className={`badge tone-${item.kind === 'approval' ? 'wait' : item.kind === 'error' ? 'warn' : 'info'}`}>{t(item.kind === 'approval' ? '◆ 待确认' : item.kind === 'error' ? '△ 异常' : '● 新结果')}</span>
            <div className="inbox-body"><strong className="mono">{item.run.id}</strong><span className="small">{item.kind === 'approval' ? item.run.title : item.kind === 'error' && item.run.alerts[0] ? alertDetail(item.run.alerts[0], t) : item.message}</span><span className="muted small">{item.at}</span>
              <a href={`#/runs/${encodeURIComponent(item.run.id)}`}>{t(item.kind === 'approval' ? '去确认' : '在工作台查看')} →</a></div>
            <button type="button" className="icon-button" title={t('标记已处理')} aria-label={t('标记 {id} 已处理', { id: item.run.id })} onClick={() => dismiss(item.key)}>✓</button>
          </div>)}{shown.length === 0 && <div className="inbox-empty">{t(items.length === 0 ? '暂无待处理事项' : '这一类暂时没有事项')}</div>}</div>
        </section>}
        {!fresh && !demo && <section className="stack" style={{ gap: 10 }}><h2>{t('进行中')} <span className="muted">· {active.length}</span></h2>
          {active.map((run) => <RunCard key={run.id} run={run} />)}
          {runs && active.length === 0 && <div className="card empty-state compact"><p className="muted">{t('当前没有进行中的运行。')}</p><a href="#/new">{t('从研究目标、想法或方案开始')} →</a></div>}
        </section>}
        {!fresh && <section className="stack" style={{ gap: 10 }}><div className="section-head"><h2>{t('全部运行')} <span className="muted">· {runs?.length ?? 0}</span></h2>
          <input type="search" className="run-search" aria-label={t('搜索运行')} placeholder={t('搜索运行')} value={query} onChange={(e) => setQuery(e.target.value)} /></div>
          {runs?.filter((r) => `${r.id} ${r.title}`.toLowerCase().includes(query.toLowerCase())).map((run) => <a className="run-row" key={run.id} href={`#/runs/${encodeURIComponent(run.id)}`}><span className="mono">{run.id}</span><StateBadge state={run.state} /><span className="small muted ellipsis">{eventText(run.last_event?.message ?? '') || run.title}</span><span aria-hidden="true">→</span></a>)}
        </section>}
      </div>
      <aside className="stack home-aside">
        <div className="card stack"><h2>{t('项目目标')}</h2><p className="prose">{current?.config.objective || String(contract.objective ?? contract.title ?? t('还没有设置项目目标。'))}</p>
          <dl className="kv"><dt>{t('数据集')}</dt><dd>{overview?.datasets.join(' · ') || '—'}</dd><dt>{t('研究执行')}</dt><dd>{t(current?.connected ? '已连接研究内核' : '尚未连接')}</dd>
            <dt>{t('模型连接')}</dt><dd className={MODEL_TONE[models.state]}>{t(MODEL_LABEL[models.state])} · <a href="#/models">{t(models.state === 'ok' ? '查看' : '去设置')}</a></dd></dl>
          {current?.builtin && <span className="tag">{t('默认工作区')}<Hint text="这是程序安装目录里的默认工作区。建议为每个研究课题新建一个独立的项目文件夹。" /></span>}
          <a href="#/project/settings">{t('编辑项目设置')} →</a></div>
        {!fresh && <div className="card stack"><h2>{t('到目前为止')}</h2>{overviewError && <ErrorNote error={overviewError} />}
          <div className="home-stats">{([['runs', '个运行'], ['routes', '条进化路线'], ['sources', '篇已检索资料'], ['passed', '轮通过完整验收']] as const).map(([key, label]) => <div key={key}><strong>{overview?.stats[key] ?? '—'}</strong><span className="muted small">{t(label)}</span></div>)}</div>
          </div>}
        {!fresh && !demo && <div className="card stack"><h2>{t('已结束')}</h2>{ended.slice(0, 6).map((run) => <a className="ended-link" key={run.id} href={`#/runs/${encodeURIComponent(run.id)}`}><span className="mono">{run.id}</span><span className="small muted">{run.kind === 'rsi' ? t('自主研究') : run.kind === 'refine' ? t('方案完善') : t('仿真验证')}</span></a>)}
          {!ended.length && <span className="muted small">{t('暂无已结束的运行。')}</span>}</div>}
      </aside>
    </div>
  </section>;
}

const MODEL_TONE: Record<ModelState, string> = {
  loading: 'muted', error: 'tone-bad-text', missing: 'tone-bad-text', untested: 'tone-warn-text', failed: 'tone-bad-text', ok: 'tone-good-text',
};

/**
 * First-run guide: models, a project folder of its own, a first short run. It disappears once the project has its own
 * folder and a run (an untested model connection alone does not keep it up), or when dismissed in this browser.
 * Unusable models (no key / no model) bring it back, since every start would fail.
 */
function GettingStarted({ models, builtin, hasRuns }: { models: ModelState; builtin: boolean; hasRuns: boolean }) {
  const t = useT();
  const steps = [
    { done: models === 'ok', warn: models === 'untested', title: '检查模型连接',
      detail: models === 'ok' ? '三个角色的连接测试都已通过。' : models === 'untested' ? 'Key 已设置，建议对每个角色做一次连接测试。' : models === 'loading' ? '正在读取模型配置…' : '提方案、编码和决策都需要模型。先设置服务地址、模型和 Key，再测试连接。',
      actions: [['#/models', models === 'ok' ? '查看模型连接' : '去设置模型连接']] },
    { done: !builtin, warn: false, title: '新建或打开项目文件夹',
      detail: builtin ? '一个研究课题一个文件夹：约定、运行、资料和报告都放在里面，可以整体拷走。现在用的是程序安装目录里的默认工作区。' : '当前项目有自己的文件夹。',
      actions: builtin ? [['#/projects/new', '新建项目'], ['#/projects', '打开已有文件夹']] : [['#/project/settings', '查看项目设置']] },
    { done: hasRuns, warn: false, title: '发起第一次运行',
      detail: '建议从方案完善开始：写下一个想法，约 2 分钟就会停在确认点，不会自动开始训练。自主研究的提方案阶段要 1 小时以上。',
      actions: [['#/refine', '从方案完善开始'], ['#/new', '查看全部方式']] },
  ];
  const [dismissed, setDismissed] = useState(() => readLocal<boolean>('mars.gettingStarted.hidden', false));
  const settled = !builtin && hasRuns && models !== 'missing';
  if (steps.every((s) => s.done) || settled || (dismissed && models !== 'missing')) return null;
  return <section className="card getting-started" aria-labelledby="getting-started">
    <div className="section-head"><h2 id="getting-started">{t('开始使用')}</h2>
      <button type="button" className="link-button" onClick={() => { writeLocal('mars.gettingStarted.hidden', true); setDismissed(true); }}>{t('不再显示')}</button></div>
    <ol>{steps.map((step, i) => <li key={step.title} className={step.done ? 'done' : step.warn ? 'warn' : ''}>
      <span className="gs-mark" aria-hidden="true">{step.done ? '✓' : i + 1}</span>
      <div className="gs-body"><strong>{t(step.title)}<Hint text={step.detail} /></strong>
        <span className="actions">{step.actions.map(([href, label], k) => <a key={href} className={k === 0 && !step.done ? 'button primary' : 'button'} href={href}>{t(label)}</a>)}</span></div>
    </li>)}</ol>
  </section>;
}

function RunCard({ run }: { run: RunSummary }) {
  const t = useT();
  return <a className="card run-card" href={`#/runs/${encodeURIComponent(run.id)}`}>
    <div className="run-card-head"><strong className="mono">{run.id}</strong><span className="chips"><KindBadge kind={run.kind} /><StateBadge state={run.state} /></span></div>
    <div className="run-title">{run.title || t('（无标题）')}</div>{needsAttention(run) && <AlertList alerts={run.alerts} compact />}
    {run.last_event && <div className="last-event"><span>{eventText(run.last_event.message)}</span><span className="muted mono small">{run.last_event.ts}</span></div>}
    <div className="chips">{run.routes.map((route) => <span key={route.id} className="chip"><span className="mono">{route.id}</span> · {t('{n} 轮', { n: route.iterations })} <RouteBadge status={route.status} /></span>)}</div>
  </a>;
}
