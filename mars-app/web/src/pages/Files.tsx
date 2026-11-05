import { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, type Artifact, type ArtifactPreview } from '../api';
import { ErrorNote, Splitter, useSplit, Hint } from '../components/ui';
import { useT } from '../prefs';

const KINDS = [['all', '全部'], ['fig', '图'], ['code', '代码差异'], ['report', '报告'], ['record', '记录']] as const;
const GLYPH: Record<string, string> = { code: '{ }', report: '¶', record: '[ ]', fig: '▱' };

export function Files() {
  const t = useT();
  const [items, setItems] = useState<Artifact[] | null>(null);
  const [kind, setKind] = useState('all');
  const [run, setRun] = useState('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const viewer = useSplit('mars.files.viewer', 440, 300, () => Math.max(300, window.innerWidth - 690), 'after');
  const refresh = () => api.artifacts().then((data) => { setItems(data); setError(''); }, (e: Error) => setError(e.message));
  useEffect(() => { void refresh(); }, []);
  const filtered = useMemo(() => (items ?? []).filter((item) => (kind === 'all' || kind === item.kind)
    && (run === 'all' || run === item.run_id) && `${item.title} ${item.source} ${item.path}`.toLowerCase().includes(search.toLowerCase())), [items, kind, run, search]);
  const current = filtered.find((item) => item.id === selected) ?? null;
  // Project → run → route → iteration, folded unless a filter or search already narrows the list.
  const narrowed = run !== 'all' || search.trim() !== '' || kind !== 'all';
  const groups = useMemo(() => {
    const byRun = new Map<string, { count: number; general: Artifact[]; step1: Artifact[]; routes: Record<string, Record<string, Artifact[]>> }>();
    for (const item of filtered) {
      const group = byRun.get(item.run_id) ?? { count: 0, general: [], step1: [], routes: {} };
      group.count += 1;
      if (item.route && item.iteration != null) ((group.routes[item.route] ??= {})[String(item.iteration)] ??= []).push(item);
      else (item.step1_round ? group.step1 : group.general).push(item);
      byRun.set(item.run_id, group);
    }
    return [...byRun.entries()];
  }, [filtered]);
  return <section className="library-page">
    <div className="page-head"><h1>{t('产物库')}<Hint text="本项目所有运行的产物 · 只读，原文件保留在项目文件夹里" /></h1><button type="button" onClick={refresh}>{t('刷新')}</button></div>
    {error && <ErrorNote error={error} />}
    <div className="library-filters"><div className="pills" role="group" aria-label={t('产物类型')}>{KINDS.map(([id, name]) =>
      <button type="button" key={id} className={kind === id ? 'on' : ''} aria-pressed={kind === id} onClick={() => { setKind(id); setSelected(''); }}>
        {t(name)} <span className="muted">{items?.filter((item) => id === 'all' || item.kind === id).length ?? 0}</span>
      </button>)}</div><div className="actions">
      <select aria-label={t('筛选运行')} value={run} onChange={(e) => { setRun(e.target.value); setSelected(''); }}><option value="all">{t('全部运行')}</option>
        {[...new Set(items?.map((item) => item.run_id).filter(Boolean))].map((id) => <option key={id}>{id}</option>)}</select>
      <input type="search" aria-label={t('搜索产物')} placeholder={t('搜索名称、路线或文件路径')} value={search} onChange={(e) => setSearch(e.target.value)} />
    </div></div>
    {items === null && !error && <p className="muted">{t('正在读取…')}</p>}
    {items && <div className="library-layout" style={{ gridTemplateColumns: `minmax(0, 1fr) 6px ${viewer.width}px` }}>
      <div className="artifact-tree" aria-label={t('产物列表')}>
        {groups.map(([runId, group]) => <details key={runId || 'reports'} className="lib-group" open={narrowed || groups.length === 1}>
          <summary><span className="mono">{runId || t('项目报告')}</span><span className="muted small">{group.count}</span></summary>
          {group.general.map((item) => <Row key={item.id} item={item} selected={current?.id === item.id} onPick={setSelected} />)}
          {group.step1.length > 0 && <details className="lib-group nested" open={narrowed}>
            <summary>Step1<span className="muted small">{group.step1.length}</span></summary>
            {[...group.step1].sort((x, y) => x.title.localeCompare(y.title)).map((item) => <Row key={item.id} item={item} selected={current?.id === item.id} onPick={setSelected} />)}
          </details>}
          {Object.entries(group.routes).sort(([x], [y]) => x.localeCompare(y, undefined, { numeric: true })).map(([route, iterations]) => <details key={route} className="lib-group nested" open={narrowed}>
            <summary><span className="mono">{route}</span><span className="muted small">{Object.values(iterations).flat().length}</span></summary>
            {Object.entries(iterations).sort((x, y) => Number(x[0]) - Number(y[0])).map(([k, rows]) => <details key={k} className="lib-group nested" open={narrowed}>
              <summary>{t('第 {k} 轮', { k })}<span className="muted small">{rows.length}</span></summary>
              {rows.map((item) => <Row key={item.id} item={item} selected={current?.id === item.id} onPick={setSelected} />)}
            </details>)}
          </details>)}
        </details>)}
        {filtered.length === 0 && <div className="empty-state"><span aria-hidden="true">▱</span><h3>{t(items.length ? '没有匹配的产物' : '还没有产物')}</h3></div>}
      </div>
      <Splitter dragging={viewer.dragging} handle={viewer.handle} label="拖动调整查看器宽度，双击恢复" />
      <aside className="artifact-viewer">{current ? <ArtifactView key={current.id} item={current} /> : <p className="muted">{t('选择一项产物')}</p>}</aside>
    </div>}
  </section>;
}

/** One artifact as a row; the label drops the route and iteration already given by its group. */
function Row({ item, selected, onPick }: { item: Artifact; selected: boolean; onPick: (id: string) => void }) {
  const t = useT();
  const label = item.route ? item.title.split(' · ').slice(2).join(' · ') || item.title : item.title;
  return <button type="button" className={`lib-row${selected ? ' selected' : ''}`} aria-pressed={selected} onClick={() => onPick(item.id)}>
    <span className="artifact-glyph mono" aria-hidden="true">{GLYPH[item.kind]}</span>
    <span className="ellipsis">{label}</span>
    <span className="tag">{t(KINDS.find(([id]) => id === item.kind)?.[1] ?? item.kind)}</span>
  </button>;
}

function ArtifactView({ item }: { item: Artifact }) {
  const t = useT();
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let alive = true;
    api.artifactPreview(item.id).then((found) => { if (alive) setPreview(found); }, (e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [item.id]);
  const reveal = async () => {
    try { await api.artifactReveal(item.id); setNotice(t('已在系统文件管理器中显示。')); } catch (e) { setError((e as Error).message); }
  };
  const link = `#/runs/${encodeURIComponent(item.run_id)}${item.route ? `?route=${encodeURIComponent(item.route)}&iteration=${item.iteration}&viewer=${item.kind === 'code' ? 'code' : item.kind === 'fig' ? 'eval' : 'record'}` : ''}`;
  return <div className="stack">
    <span className="small muted">{t(KINDS.find(([id]) => id === item.kind)?.[1] ?? item.kind)}</span>
    <h2>{item.title}</h2>
    {error && <ErrorNote error={error} />}
    {!preview && !error && <p className="muted">{t('正在读取…')}</p>}
    {preview?.format === 'image' && <a href={api.artifactUrl(item.id)} target="_blank" rel="noreferrer"><img className="artifact-image" src={api.artifactUrl(item.id)} alt={item.title} /></a>}
    {preview?.format === 'markdown' && <div className="markdown artifact-text"><ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.text ?? ''}</ReactMarkdown></div>}
    {preview?.format === 'text' && <pre className="code-block artifact-text">{preview.text}</pre>}
    {preview?.format === 'diff' && <div className="stack">{preview.diff?.files.map((file) => <details key={file.path} open><summary className="mono">{file.path}</summary><pre className="diff">{file.patch.split('\n').map((line, i) => <span key={i} className={line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : line.startsWith('@@') ? 'hunk' : ''}>{line || ' '}</span>)}</pre>{file.truncated && <span className="small muted">{t('此文件差异较长，预览已截断。')}</span>}</details>)}</div>}
    {preview?.format === 'download' && <div className="empty-state compact"><span className="artifact-glyph mono">{item.extension.slice(1).toUpperCase()}</span><p className="muted">{t('下载后可用系统应用打开此文件。')}</p></div>}
    {preview?.truncated && <span className="small muted">{t('预览已截断，请下载查看完整文件。')}</span>}
    <dl className="kv"><dt>{t('来自')}</dt><dd>{item.source || t('项目报告')}</dd><dt>{t('路径')}</dt><dd className="mono">{item.path}</dd>
      <dt>{t('修改时间')}</dt><dd>{new Date(item.modified * 1000).toLocaleString()}</dd><dt>{t('文件大小')}</dt><dd>{item.size === null ? '—' : item.size < 1024 ? `${item.size} B` : `${(item.size / 1024).toFixed(1)} KB`}</dd></dl>
    <div className="actions">{item.run_id && <a className="button primary" href={link}>{t('在运行中查看')}</a>}
      <a className="button" href={api.artifactUrl(item.id, true)} download>{t('下载')}</a>
      {!item.virtual && <button type="button" onClick={reveal}>{t('在文件管理器中显示')}</button>}</div>
    {notice && <span role="status" className="small muted">{notice}</span>}
  </div>;
}
