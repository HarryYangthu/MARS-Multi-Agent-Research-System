import { useEffect, useState } from 'react';
import { api, setApiProject, type ModelsView, type PreflightCheck, type ProjectConfig } from '../api';
import { PathPicker } from '../components/PathPicker';
import { ErrorNote, Hint } from '../components/ui';
import { readLocal, useProjects, writeLocal } from '../projects';
import { useT } from '../prefs';
import { useConfirm } from '../components/Confirm';

const STEPS = ['项目文件夹', '模型连接', '代码与资料', '研究数据', '执行环境', '预检并创建'];
const EMPTY: ProjectConfig = { name: '', objective: '', repo: { path: '', ref: 'HEAD', commit: '', editable_files: [] },
  datasets: [], context_sources: [], remote: { enabled: false, name: '', device: 'cuda:0', parallel: 2, data_path: '' }, model_mode: 'shared' };

/** What each folder of a project holds (created when the project is created or moved). */
const FOLDERS: [string, string][] = [
  ['.mars/project.yaml', '项目声明：名称、目标、代码仓、数据集和执行环境'],
  ['.mars/project_id', '项目编号，由内核生成；移动文件夹后靠它认出同一个项目'],
  ['context/', '背景资料：论文、笔记等，研究时引用'],
  ['tasks/', '研究约定：每次运行的目标、门槛和预算'],
  ['runs/', '运行产物：事件、提案、每轮记录、评测图和报告'],
  ['reports/', '导出的 Word / PPT / Excel 报告'],
  ['workspace/', '内核工作区：基线副本、候选代码和评测缓存，不要手动修改'],
];

export function ProjectSwitch() {
  const t = useT();
  const { current, projects, select, reload, error: loadError } = useProjects();
  const [picker, setPicker] = useState(false);
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const open = async (value: string) => {
    setBusy(true); setError('');
    try { const found = await api.openProject(value); await reload(found.id); window.location.hash = '#/'; }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <section className="stack project-switch-page">
    <div className="page-head"><div><h1>{t('项目切换')}</h1><p className="muted">{t('最近打开')}</p></div></div>
    {(error || loadError) && <ErrorNote error={error || loadError} />}
    <div className="project-tiles">{projects.map((project) => <button type="button" key={project.id}
      className={`project-tile card${project.id === current?.id ? ' selected' : ''}`} onClick={() => select(project.id)}>
      <div className="section-head"><strong>{project.name}</strong>{project.id === current?.id && <span className="badge tone-info">{t('当前')}</span>}</div>
      <span className="mono small muted">{project.path}</span>
      <span className="small">{t(project.connected ? '已连接研究内核' : '项目文件与产物浏览')}</span>
    </button>)}</div>
    <div className="actions"><a className="button primary" href="#/projects/new">+ {t('新建项目')}</a>
      <button type="button" onClick={() => setPicker(true)}>{t('打开已有文件夹')}</button></div>
    <form className="card form" onSubmit={(e) => { e.preventDefault(); void open(path); }}>
      <label><span>{t('项目文件夹路径')}<Hint text="已有项目会直接打开；普通文件夹会添加项目声明，保留原有文件。" /></span><input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/…/my-research" /></label>
      <div className="actions"><button disabled={busy || !path.trim()}>{busy ? t('正在打开…') : t('打开项目')}</button></div>
    </form>
    {picker && <PathPicker onClose={() => setPicker(false)} onPick={(p) => { setPath(p); void open(p); }} />}
  </section>;
}

export function NewProject({ editing = false }: { editing?: boolean }) {
  const t = useT();
  const confirm = useConfirm();
  const { current, defaultParent, reload } = useProjects();
  const draftKey = editing ? `mars.project.settings.${current?.id}` : 'mars.project.draft';
  const [draft] = useState(() => readLocal<{ config: ProjectConfig; path: string; step: number } | null>(draftKey, null));
  const [config, setConfig] = useState<ProjectConfig>(() => editing && current ? { ...EMPTY, ...current.config, name: current.name } : draft?.config ?? EMPTY);
  const [path, setPath] = useState(() => editing ? current?.path ?? '' : draft?.path ?? `${defaultParent}/`);
  const [step, setStep] = useState(draft?.step ?? 0);
  const [checks, setChecks] = useState<PreflightCheck[] | null>(null);
  const [checkedFor, setCheckedFor] = useState('');
  const [models, setModels] = useState<ModelsView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [picker, setPicker] = useState<{ files: boolean; initial?: string; choose: (p: string) => void } | null>(null);
  useEffect(() => { api.models().then(setModels, () => undefined); }, []);
  useEffect(() => { writeLocal(draftKey, { config, path, step }); setChecks(null); }, [config, path]);
  useEffect(() => { writeLocal(draftKey, { config, path, step }); }, [step]);
  const patch = (values: Partial<ProjectConfig>) => setConfig((c) => ({ ...c, ...values }));
  const repo = config.repo ?? EMPTY.repo!;
  const remote = config.remote ?? EMPTY.remote!;
  const datasets = config.datasets ?? [];
  const updateDataset = (i: number, field: string, value: string) => patch({ datasets: datasets.map((d, n) => n === i ? { ...d, [field]: value } : d) });
  const choosePath = (choose: (p: string) => void, files = false, initial?: string) => setPicker({ choose, files, initial });
  const preflight = async () => {
    setBusy(true); setError('');
    const fingerprint = JSON.stringify([path, config]);
    try { setChecks((await api.projectPreflight(path, config)).checks); setCheckedFor(fingerprint); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setError('');
    try {
      let id = current?.id ?? '';
      if (editing && current && !current.builtin && path.trim() !== current.path) {
        // A new location moves the whole folder (runs, caches, .mars/project_id included); the project's id follows the path.
        if (!(await confirm(t('把整个项目文件夹从 {a} 移到 {b}？运行记录、缓存和 .mars/project_id 一起移动。有运行在进行时不能移动。', { a: current.path, b: path.trim() })))) return;
        id = (await api.moveProject(current.id, path.trim())).id;
        setApiProject(id);
      }
      const found = editing && current ? await api.saveProject(id, config) : await api.createProject(path, config);
      writeLocal(draftKey, null); await reload(found.id); window.location.hash = '#/';
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <section>
    <div className="page-head"><h1>{t(editing ? '项目设置' : '新建项目')}</h1><a className="button" href="#/">{t('稍后继续')}</a></div>
    {error && <ErrorNote error={error} />}
    <div className="wizard">
      <nav className="wizard-steps" aria-label={t('项目设置步骤')}>{STEPS.map((name, i) => <button type="button" key={name}
        aria-current={step === i ? 'step' : undefined} className={step === i ? 'on' : ''} onClick={() => setStep(i)}>
        <span className={`step-number${i < step ? ' done' : ''}`}>{i < step ? '✓' : i + 1}</span>{t(editing && i === 5 ? '预检并保存' : name)}
      </button>)}</nav>
      <div className="card form wizard-content">
        <h2>{t(editing && step === 5 ? '预检并保存' : STEPS[step])}</h2>
        {step === 0 && <>
          <label>{t('项目名称')}<input value={config.name} onChange={(e) => {
            const name = e.target.value; patch({ name });
            if (!editing && (path === `${defaultParent}/` || path === `${defaultParent}/${config.name}`)) setPath(`${defaultParent}/${name}`);
          }} /></label>
          <label>{t('保存位置')}<div className="path-bar"><input value={path} disabled={editing && current?.builtin} onChange={(e) => setPath(e.target.value)} />
            {!(editing && current?.builtin) && <button type="button" onClick={() => choosePath((parent) => setPath(`${parent}/${config.name || 'my-research'}`))}>{t('浏览…')}</button>}</div></label>
          {editing && !current?.builtin && path.trim() !== current?.path && <span className="small tone-warn-text">{t('保存时会把整个项目文件夹移到新位置。')}</span>}
          <label>{t('项目目标')}<textarea rows={4} value={config.objective ?? ''} onChange={(e) => patch({ objective: e.target.value })} /></label>
          <dl className="folder-tree">{FOLDERS.map(([name, what]) => <div key={name}><dt className="mono">{name}</dt><dd>{t(what)}</dd></div>)}</dl>
          {editing && current?.builtin && <span className="tag">{t('默认工作区')}<Hint text="这是当前内核工作区。此页只保存应用层项目信息，不改动内核配置。" /></span>}
        </>}
        {step === 1 && <>
          {models && <div className="table-wrap"><table><thead><tr><th>{t('角色')}</th><th>{t('模型')}</th><th>Key</th></tr></thead><tbody>
            {Object.entries(models.roles).map(([role, value]) => <tr key={role}><td className="mono">{role}</td><td>{value.model || '—'}</td><td>{t(models.keys[models.providers[value.provider ?? '']?.api_key_env ?? '']?.set ? '已设置' : '未设置')}</td></tr>)}
          </tbody></table></div>}
          {!models && <p className="muted">—</p>}
          <a className="button" href="#/models">{t('到模型连接页配置和测试')} →</a>
        </>}
        {step === 2 && <>
          <label>{t('研究代码仓（只读引用）')}<div className="path-bar"><input value={repo.path} onChange={(e) => patch({ repo: { ...repo, path: e.target.value } })} />
            <button type="button" onClick={() => choosePath((value) => patch({ repo: { ...repo, path: value } }))}>{t('浏览…')}</button></div></label>
          <div className="form-row"><label>{t('分支或引用')}<input value={repo.ref} onChange={(e) => patch({ repo: { ...repo, ref: e.target.value } })} /></label>
            <label>{t('固定提交')}<input className="mono" value={repo.commit} onChange={(e) => patch({ repo: { ...repo, commit: e.target.value } })} /></label></div>
          <label>{t('编码 Agent 可以修改的文件')}<textarea rows={4} className="mono" value={Array.isArray(repo.editable_files) ? repo.editable_files.join('\n') : repo.editable_files}
            onChange={(e) => patch({ repo: { ...repo, editable_files: e.target.value.split('\n') } })} placeholder="src/model.py" /></label>
          <h3>{t('背景资料')}<Hint text="保存时按原文件名归档到 context/，供研究时引用。" /></h3>
          {(config.context_sources ?? []).map((source, i) => <div className="path-bar" key={`${i}:${source}`}><span className="mono grow">{source}</span>
            <button type="button" aria-label={t('移除资料')} onClick={() => patch({ context_sources: config.context_sources?.filter((_, n) => n !== i) })}>×</button></div>)}
          <div className="actions"><button type="button" onClick={() => choosePath((p) => patch({ context_sources: [...(config.context_sources ?? []), p] }), true)}>{t('添加文件')}</button>
            <button type="button" onClick={() => choosePath((p) => patch({ context_sources: [...(config.context_sources ?? []), p] }))}>{t('添加文件夹')}</button></div>
        </>}
        {step === 3 && <>
          {datasets.map((row, i) => <fieldset className="form" key={i}><legend>{row.name || t('数据集')}</legend>
            <label>{t('数据集名称')}<input value={row.name} onChange={(e) => updateDataset(i, 'name', e.target.value)} /></label>
            {(['config', 'data'] as const).map((field) => <label key={field}>{t(field === 'config' ? '训练配置' : '数据文件')}<div className="path-bar">
              <input value={row[field]} onChange={(e) => updateDataset(i, field, e.target.value)} /><button type="button" onClick={() => choosePath((p) => updateDataset(i, field, p), true)}>{t('浏览…')}</button>
            </div></label>)}
            <button type="button" onClick={() => patch({ datasets: datasets.filter((_, n) => n !== i) })}>{t('移除数据集')}</button>
          </fieldset>)}
          <button type="button" onClick={() => patch({ datasets: [...datasets, { name: '', config: '', data: '' }] })}>+ {t('添加数据集')}</button>
        </>}
        {step === 4 && <>
          <label className="inline"><input type="checkbox" checked={!!remote.enabled} onChange={(e) => patch({ remote: { ...remote, enabled: e.target.checked } })} />{t('使用远程 GPU（SSH）')}</label>
          {remote.enabled ? <>
            <label>{t('本机设置中的服务器名称')}<input value={remote.name ?? ''} onChange={(e) => patch({ remote: { ...remote, name: e.target.value } })} /></label>
            <div className="form-row"><label>{t('设备')}<input value={remote.device ?? ''} onChange={(e) => patch({ remote: { ...remote, device: e.target.value } })} /></label>
              <label>{t('同时运行的训练数')}<input type="number" min={1} value={remote.parallel ?? 2} onChange={(e) => patch({ remote: { ...remote, parallel: Number(e.target.value) } })} /></label></div>
            <label>{t('服务器上的数据位置')}<input value={remote.data_path ?? ''} onChange={(e) => patch({ remote: { ...remote, data_path: e.target.value } })} /></label>
          </> : <p>{t('本机 CPU')}</p>}
        </>}
        {step === 5 && <>
          <span><button type="button" disabled={busy} onClick={preflight}>{busy ? t('正在检查…') : t('运行预检')}</button><Hint text="预检文件、目录权限和固定代码版本；不会调用模型或执行训练。" /></span>
          {checks && <ul className="check-list">{checks.map((c) => <li key={c.key} className={`check-${c.status}`}><span>{c.status === 'pass' ? '✓' : c.status === 'warn' ? '△' : '✕'}</span><div><strong>{t(c.title)}</strong><p>{c.detail}</p></div></li>)}</ul>}
          <button type="button" className="primary" disabled={busy || !config.name.trim() || !path.trim() || (!editing && (!checks || checkedFor !== JSON.stringify([path, config]) || checks.some((c) => c.status === 'error')))} onClick={save}>
            {busy ? t('正在保存…') : t(editing ? '保存项目设置' : '创建项目')}
          </button>
        </>}
        <div className="wizard-footer"><button type="button" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>{t('上一步')}</button>
          {step < STEPS.length - 1 && <button type="button" className="primary" onClick={() => setStep((s) => s + 1)}>{t(step === 4 ? '去预检' : '下一步')}</button>}</div>
      </div>
    </div>
    {picker && <PathPicker initial={picker.initial} files={picker.files} onClose={() => setPicker(null)} onPick={picker.choose} />}
  </section>;
}
