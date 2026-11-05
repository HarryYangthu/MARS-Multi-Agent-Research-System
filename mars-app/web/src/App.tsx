import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { api, type RunSummary } from './api';
import { RunList } from './pages/RunList';
import { RunView } from './pages/RunView';
import { NewRun } from './pages/NewRun';
import { HpoList, HpoView } from './pages/Hpo';
import { VerifyList, VerifyView } from './pages/Verify';
import { Models } from './pages/Models';
import { Compute } from './pages/Compute';
import { Files } from './pages/Files';
import { NewProject, ProjectSwitch } from './pages/Projects';
import { useProjects } from './projects';
import { needsAttention, useInbox } from './activity';
import { ErrorNote, Splitter, useSplit, eventText } from './components/ui';
import { useAlertNotifications } from './notify';
import { usePrefs, useT, type Theme } from './prefs';

type Route =
  | { page: 'list' }
  | { page: 'run'; id: string }
  | { page: 'new' }
  | { page: 'research' }
  | { page: 'refine' }
  | { page: 'models' }
  | { page: 'compute' }
  | { page: 'files' }
  | { page: 'projects' }
  | { page: 'project-new' }
  | { page: 'project-settings' }
  | { page: 'settings' }
  | { page: 'verify' }
  | { page: 'verify-new' }
  | { page: 'verify-view'; id: string }
  | { page: 'hpo' }
  | { page: 'hpo-new' }
  | { page: 'hpo-view'; id: string };

function parse(hash: string): Route {
  const parts = hash.split('?')[0].replace(/^#\/?/, '').split('/').filter(Boolean).map((value) => {
    try { return decodeURIComponent(value); } catch { return value; }
  });
  if (parts[0] === 'projects' && parts[1] === 'new') return { page: 'project-new' };
  if (parts[0] === 'projects') return { page: 'projects' };
  if (parts[0] === 'project' && parts[1] === 'settings') return { page: 'project-settings' };
  if (parts[0] === 'settings') return { page: 'settings' };
  if (parts[0] === 'files') return { page: 'files' };
  if (parts[0] === 'runs' && parts[1]) return { page: 'run', id: parts[1] };
  if (parts[0] === 'new') return { page: 'new' };
  if (parts[0] === 'research') return { page: 'research' };
  if (parts[0] === 'refine') return { page: 'refine' };
  if (parts[0] === 'models') return { page: 'models' };
  if (parts[0] === 'compute') return { page: 'compute' };
  if (parts[0] === 'verify' && parts[1] === 'new') return { page: 'verify-new' };
  if (parts[0] === 'verify' && parts[1]) return { page: 'verify-view', id: parts[1] };
  if (parts[0] === 'verify') return { page: 'verify' };
  if (parts[0] === 'hpo' && parts[1] === 'new') return { page: 'hpo-new' };
  if (parts[0] === 'hpo' && parts[1]) return { page: 'hpo-view', id: parts[1] };
  if (parts[0] === 'hpo') return { page: 'hpo' };
  return { page: 'list' };
}

export function App() {
  const { current, error } = useProjects();
  const t = useT();
  if (!current) return <div className="page">{error ? <ErrorNote error={error} /> : t('正在打开项目…')}</div>;
  return <Workspace key={current.id} />;
}

function Workspace() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runsError, setRunsError] = useState('');
  const { current } = useProjects();
  const sidebar = useSplit('mars.sidebar', 248, 190, () => Math.min(420, window.innerWidth - 640), 'before');

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const loadRuns = useCallback(() => {
    api.runs().then((items) => (setRuns(items), setRunsError('')), (e: Error) => setRunsError(e.message));
  }, []);
  useEffect(() => {
    loadRuns();
    const timer = window.setInterval(loadRuns, 10000);
    return () => window.clearInterval(timer);
  }, [loadRuns]);

  return (
    <div className={sidebar.dragging ? 'app dragging' : 'app'} style={{ gridTemplateColumns: `${sidebar.width}px 6px minmax(0, 1fr)` }}>
      <Sidebar route={route} runs={runs} />
      <Splitter dragging={sidebar.dragging} handle={sidebar.handle} label="拖动调整侧边栏宽度，双击恢复" />
      <main className="content" style={{ '--sidebar-width': `${sidebar.width}px` } as CSSProperties}>
        {route.page === 'run' ? (
          <RunView id={route.id} sidebarWidth={sidebar.width} />
        ) : (
          <div className="page">
            <BackLink route={route} />
            {route.page === 'list' && <RunList runs={runs} error={runsError} />}
            {route.page === 'new' && <NewRun />}
            {route.page === 'research' && <NewRun scenario="rsi" />}
            {route.page === 'refine' && <NewRun scenario="refine" />}
            {route.page === 'files' && <Files />}
            {route.page === 'projects' && <ProjectSwitch />}
            {route.page === 'project-new' && <NewProject key="new" />}
            {route.page === 'project-settings' && <NewProject key={current?.id} editing />}
            {route.page === 'settings' && <SettingsHome />}
            {route.page === 'models' && <Models />}
            {route.page === 'compute' && <Compute />}
            {route.page === 'verify' && <VerifyList />}
            {route.page === 'verify-new' && <NewRun scenario="verify" />}
            {route.page === 'verify-view' && <VerifyView id={route.id} />}
            {route.page === 'hpo' && <HpoList />}
            {route.page === 'hpo-new' && <NewRun scenario="hpo" />}
            {route.page === 'hpo-view' && <HpoView id={route.id} />}
          </div>
        )}
      </main>
    </div>
  );
}

function Sidebar({ route, runs }: { route: Route; runs: RunSummary[] | null }) {
  const t = useT();
  const { theme, setTheme, lang, setLang } = usePrefs();
  const notifications = useAlertNotifications();
  const { current, projects, select } = useProjects();
  const inbox = useInbox(runs);
  const creating = route.page === 'project-new';
  const flagged = new Set(inbox.items.map((item) => item.run.id));
  const attention = runs?.filter((r) => flagged.has(r.id)) ?? [];
  const running = runs?.filter((r) => !flagged.has(r.id) && r.state === 'running') ?? [];
  const ended = runs?.filter((r) => !flagged.has(r.id) && r.state !== 'running') ?? [];
  const currentRun = route.page === 'run' ? route.id : null;
  const nav: [string, string, boolean][] = [
    ['#/', '首页', route.page === 'list'],
    ['#/new', '新建', ['new', 'research', 'refine', 'verify-new', 'hpo-new'].includes(route.page)],
    ['#/files', '产物库', route.page === 'files'],
    ['#/compute', '算力', route.page === 'compute'],
    ['#/settings', '设置', ['settings', 'models', 'project-settings'].includes(route.page)],
  ];
  const themes: [Theme, string][] = [['light', '浅色'], ['dark', '深色'], ['system', '跟随系统']];

  const runLink = (run: RunSummary, note: string) => (
    <a key={run.id} className={run.id === currentRun ? 'side-run active' : 'side-run'} href={`#/runs/${encodeURIComponent(run.id)}`}
      aria-current={run.id === currentRun ? 'page' : undefined}>
      <span className="mono">{run.id}</span>
      {note && <span className="small">{note}</span>}
    </a>
  );
  const attentionNote = (run: RunSummary) =>
    run.state === 'waiting_approval' ? `◆ ${t('等待确认')}` : !needsAttention(run) ? `● ${t('新结果')}` : run.alerts[0] ? `△ ${t(run.alerts[0].code === 'stopped' ? '进程已退出' : run.alerts[0].code === 'stalled' ? '可能停滞' : '模型服务异常')}` : '';

  return (
    <aside className="sidebar" aria-label={t('导航')}>
      <a className="brand" href="#/">MARS <span className="small muted">RESEARCH</span></a>
      {creating ? <div className="project-creating"><span className="small muted">{t('项目')}</span><strong>{t('新项目 · 设置中')}</strong></div> : <details className="project-menu" key={current?.id}>
        <summary aria-label={t('切换项目')}><span><span className="small muted">{t('项目')}</span><strong>{current?.name}</strong>{current?.demo && <span className="tag">{t('演示')}</span>}</span><span aria-hidden="true">⌄</span></summary>
        <div className="project-popover">
          <span className="small muted">{t('最近打开')}</span>
          {projects.map((project) => <button type="button" key={project.id} className={current?.id === project.id ? 'selected' : ''} onClick={(e) => {
            select(project.id); const details = e.currentTarget.closest('details'); if (details) details.open = false;
          }}><strong>{project.name}</strong><span className="small muted ellipsis">{project.path}</span></button>)}
          <a href="#/projects/new" onClick={(e) => { const details = e.currentTarget.closest('details'); if (details) details.open = false; }}>+ {t('新建项目')}</a>
          <a href="#/projects" onClick={(e) => { const details = e.currentTarget.closest('details'); if (details) details.open = false; }}>{t('打开已有文件夹')}</a>
        </div>
      </details>}
      <nav className="side-nav" aria-label={t('功能')}>
        {nav.filter(([href]) => !(current?.demo && href === '#/new')).map(([href, label, active]) => (
          <a key={href} href={href} className={active ? 'active' : ''} aria-current={active ? 'page' : undefined}
            aria-disabled={creating || undefined} tabIndex={creating ? -1 : undefined} onClick={(event) => { if (creating) event.preventDefault(); }}>
            <span className="nav-label"><span className="nav-glyph" aria-hidden="true">{href === '#/' ? '⌂' : href === '#/new' ? '+' : href === '#/files' ? '▧' : href === '#/compute' ? '▣' : '⚙'}</span>{t(label)}</span>
            {href === '#/' && inbox.items.length > 0 && <span className="count">{inbox.items.length}</span>}
          </a>
        ))}
      </nav>
      <div className="side-runs">
        {creating ? <span className="side-group">{t('暂无运行')}</span> : <>
        {runs === null && <span className="side-group">{t('正在读取…')}</span>}
        {attention.length > 0 && <span className="side-group attention">{t('待处理')} · {attention.length}</span>}
        {attention.map((r) => runLink(r, attentionNote(r)))}
        {running.length > 0 && <span className="side-group">{t('进行中')} · {running.length}</span>}
        {running.map((r) => runLink(r, eventText(r.last_event?.message ?? '')))}
        {ended.length > 0 && <span className="side-group">{t('已结束')} · {ended.length}</span>}
        {ended.map((r) => runLink(r, ''))}
        </>}
      </div>
      <div className="side-foot">
        <div className="seg" role="group" aria-label={t('主题')}>
          {themes.map(([value, label]) => (
            <button key={value} type="button" className={theme === value ? 'on' : ''} aria-pressed={theme === value} onClick={() => setTheme(value)}>
              {t(label)}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Language">
          <button type="button" className={lang === 'zh' ? 'on' : ''} aria-pressed={lang === 'zh'} onClick={() => setLang('zh')}>中文</button>
          <button type="button" className={lang === 'en' ? 'on' : ''} aria-pressed={lang === 'en'} onClick={() => setLang('en')}>English</button>
        </div>
        <div className="small">
          {notifications.permission === 'default' && (
            <button onClick={notifications.request} title={t('运行出现余额不足、停滞或意外退出时弹出系统通知（需保持本页打开）')}>
              {t('开启系统通知')}
            </button>
          )}
          {notifications.permission === 'granted' && <span className="muted">{t('系统通知已开启')}</span>}
          {notifications.permission === 'denied' && <span className="muted">{t('系统通知被浏览器禁止')}</span>}
        </div>
      </div>
    </aside>
  );
}

/** One level up from every page except home; detail pages of searches and checks have their own list link. */
const PARENT: Partial<Record<Route['page'], [string, string]>> = {
  new: ['#/', '返回首页'], research: ['#/new', '返回新建'], refine: ['#/new', '返回新建'], 'verify-new': ['#/new', '返回新建'], 'hpo-new': ['#/new', '返回新建'],
  files: ['#/', '返回首页'], compute: ['#/', '返回首页'], settings: ['#/', '返回首页'], projects: ['#/', '返回首页'],
  'project-new': ['#/', '返回首页'], 'project-settings': ['#/settings', '返回设置'], models: ['#/settings', '返回设置'],
  verify: ['#/', '返回首页'], hpo: ['#/', '返回首页'],
};

function BackLink({ route }: { route: Route }) {
  const t = useT();
  const parent = PARENT[route.page];
  return parent ? <a className="back-link" href={parent[0]}>← {t(parent[1])}</a> : null;
}

function SettingsHome() {
  const t = useT();
  const { current } = useProjects();
  return <section className="stack"><h1>{t('设置')}</h1><div className="project-tiles">
    <a className="card run-card" href="#/project/settings"><h2>{t('项目设置')}</h2><span className="muted">{current?.name}</span></a>
    <a className="card run-card" href="#/models"><h2>{t('模型连接')}</h2></a>
  </div></section>;
}
