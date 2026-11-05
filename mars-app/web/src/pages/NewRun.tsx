import { useEffect, useState } from 'react';
import { api, openStartedRun, type Declaration, type Template } from '../api';
import { metricLabel } from '../metrics';
import { ErrorNote, Hint } from '../components/ui';
import { ExecutionEnvironmentPicker, useExecutionEnvironment } from '../components/ExecutionEnvironment';
import { useT } from '../prefs';
import { useConfirm } from '../components/Confirm';
import { readLocal, useProjects, writeLocal } from '../projects';
import { Refine } from './Refine';
import { VerifyNew } from './Verify';
import { HpoNew } from './Hpo';
import { RsiFlow } from '../components/RsiFlow';
import { missingModels } from '../modelStatus';

export type Scenario = 'rsi' | 'refine' | 'verify' | 'hpo';
// Ordered by how quickly a first-time user sees a result; the rough durations are what the user waits for.
const SCENARIOS: { id: Scenario; when: string; name: string; what: string; time: string; href: string }[] = [
  { id: 'refine', when: '有一个粗略想法', name: '方案完善', what: '结构化你的想法，在方案附近短预算进化。', time: '约 2 分钟', href: '#/refine' },
  { id: 'verify', when: '方案已经成熟', name: '仿真验证', what: '实现候选，和基线在同样预算下训练对比。', time: '约 10–40 分钟', href: '#/verify/new' },
  { id: 'hpo', when: '结构不变，只调训练配置', name: '超参寻优', what: '在可修改的训练配置范围内搜索超参数。', time: '每次试验一次训练', href: '#/hpo/new' },
  { id: 'rsi', when: '只有目标和约束', name: '自主研究', what: '调研、提方案、辩论评审，再逐轮进化。', time: '1 小时以上', href: '#/research' },
];
type MetricGate = { metric: string; max_worse?: number };
type CheckGate = { check: string; field: string; max?: number; min?: number; stop?: boolean };
type Targets = { gates?: MetricGate[]; checks?: CheckGate[] };
type Spec = Record<string, unknown> & { id?: string; title?: string; objective?: string; targets?: Targets; budget?: Record<string, unknown>; constraints?: string[] };
const num = (value: unknown, fallback: number) => (typeof value === 'number' && Number.isFinite(value) ? value : fallback);

/** A gap against the baseline without asking for negative numbers: "允许劣化 x" or "要求改善 x". */
function MarginInput({ label, unit, value, onChange }: { label: string; unit?: string; value: number; onChange: (value: number) => void }) {
  const t = useT();
  const improve = value < 0;
  return <label>{label}<span className="margin-input">
    <select value={improve ? 'improve' : 'allow'} onChange={(e) => onChange(e.target.value === 'improve' ? -Math.abs(value) : Math.abs(value))}>
      <option value="allow">{t('最多允许劣化')}</option><option value="improve">{t('至少要求改善')}</option>
    </select>
    <input type="number" min={0} step="any" value={Math.abs(value)} onChange={(e) => onChange((improve ? -1 : 1) * Math.abs(Number(e.target.value)))} />
    {unit && <span className="muted">{unit}</span>}</span></label>;
}
const BUDGETS = [['proposers', '独立提案数'], ['routes', '进化路线数'], ['max_iterations_per_route', '每条路线最多轮数'], ['max_step1_rounds', '提方案轮数上限'], ['research_sources', '精读文献数上限'], ['codingagent_minutes', '每轮编码时间（分钟）']];

export function NewRun({ scenario = 'refine' }: { scenario?: Scenario }) {
  const t = useT();
  const { current } = useProjects();
  return <section>
    <div className="page-head"><div><h1>{t('新建运行')}</h1></div>
      <div className="actions"><a className="button" href="#/verify">{t('验证任务')}</a><a className="button" href="#/hpo">{t('调参任务')}</a></div></div>
    <div className="scenario-grid">{SCENARIOS.map((item) => <a key={item.id} href={item.href} className={`scenario-card${scenario === item.id ? ' selected' : ''}`} aria-current={scenario === item.id ? 'page' : undefined}>
      <strong title={`${t(item.when)} · ${t(item.what)}`}>{t(item.name)}</strong><span className="tag">{t(item.time)}</span>
    </a>)}</div>
    {current?.demo ? <div className="note note-warn">{t('演示项目（只读）：请切换到自己的项目再新建运行。')}</div> : !current?.connected && <div className="note note-warn">{t('此项目尚未连接研究执行环境。')} <a href="#/project/settings">{t('项目设置')} →</a></div>}
    {(current?.connected || (scenario === 'rsi' && !current?.demo)) && <div className="scenario-form" key={scenario}>{scenario === 'rsi' ? <ResearchForm /> : scenario === 'refine' ? <Refine /> : scenario === 'verify' ? <VerifyNew /> : <HpoNew />}</div>}
  </section>;
}

function defaultId(name: string): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}-${name}`.slice(0, 80);
}

function ResearchForm() {
  const confirm = useConfirm();
  const t = useT();
  const { current } = useProjects();
  const draftKey = `mars.contract.draft.${current?.id}`;
  const [saved] = useState(() => readLocal<{ spec: Spec; runId: string; humanApproval: boolean; mode?: 'form' | 'yaml'; yaml?: string } | null>(draftKey, null));
  const [templates, setTemplates] = useState<Template[]>([]);
  const [chosen, setChosen] = useState('');
  const [spec, setSpec] = useState<Spec | null>(saved?.spec ?? null);
  const [runId, setRunId] = useState(saved?.runId ?? '');
  const [humanApproval, setHumanApproval] = useState(saved?.humanApproval ?? true);
  const [mode, setMode] = useState<'form' | 'yaml'>(saved?.mode ?? 'form');
  const [yaml, setYaml] = useState(saved?.yaml ?? '');
  const [preset, setPreset] = useState('standard');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(saved ? t('已恢复本机草稿。') : '');
  const execution = useExecutionEnvironment(saved?.spec?.execution && (saved.spec.execution as { environment?: string }).environment === 'remote' ? 'remote' : 'local');
  const pick = (template: Template) => {
    setChosen(template.name); setSpec(template.contract); setYaml(template.yaml); setRunId(defaultId(template.name));
    setHumanApproval(template.human_approval); setPreset('standard'); setMode('form');
  };
  useEffect(() => {
    let alive = true;
    api.templates().then((items) => {
      if (!alive) return;
      setTemplates(items);
      const preferred = items.find((x) => x.name === 'research') ?? items[0];
      if (preferred) { if (!saved) pick(preferred); else setChosen(preferred.name); }
    }, (e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, []);
  useEffect(() => { if (spec) writeLocal(draftKey, { spec: { ...spec, execution: { environment: execution.environment } }, runId, humanApproval, mode, yaml }); }, [spec, runId, humanApproval, mode, yaml, draftKey, execution.environment]);
  const patch = (value: Partial<Spec>) => setSpec((s) => ({ ...s, ...value }));
  const switchMode = async () => {
    setBusy(true); setError('');
    try {
      const result = await api.checkContract({ ...(mode === 'form' ? { contract: spec ?? {} } : { yaml }), environment: execution.environment });
      if (mode === 'yaml' && (!result.contract || typeof result.contract !== 'object' || Array.isArray(result.contract))) throw new Error(t('研究约定必须是一个 YAML 映射'));
      setYaml(result.yaml); setSpec(result.contract); setMode(mode === 'form' ? 'yaml' : 'form');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const submit = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(runId.trim())) throw new Error(t('运行 id 只能包含字母、数字、点、下划线和连字符，且不超过 80 个字符'));
      const checked = await api.checkContract({ ...(mode === 'yaml' ? { yaml } : { contract: spec ?? {} }), environment: execution.environment });
      if (checked.errors.length) throw new Error(checked.errors.join('；'));
      const missing = await missingModels();
      if (missing.length) throw new Error(t('模型还没配置好，启动后会立刻失败：{p}。请先到“设置 → 模型连接”补全。', { p: missing.map((m) => t(m.text, m.params)).join('；') }));
      if (!(await confirm(t('启动运行 {id}？这会真实调用模型并在所选环境中训练，按研究约定的预算消耗资源。', { id: runId })))) return;
      const result = await api.start(runId.trim(), checked.yaml, humanApproval);
      writeLocal(draftKey, null); await openStartedRun(result.run_id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const saveDraft = async () => {
    if (!current) return;
    setBusy(true); setError('');
    try {
      const checked = await api.checkContract({ ...(mode === 'yaml' ? { yaml } : { contract: spec ?? {} }), environment: execution.environment });
      if (checked.errors.length) throw new Error(checked.errors.join('；'));
      const found = await api.saveContract(current.id, runId.trim(), checked.yaml, humanApproval);
      setNotice(t('研究约定已保存：{path}', { path: found.path }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const applyPreset = (value: string) => {
    setPreset(value);
    if (value === 'custom') return;
    const templateBudget = templates.find((x) => x.name === chosen)?.contract.budget as Record<string, unknown> | undefined;
    patch({ budget: { ...spec?.budget, ...templateBudget, ...(value === 'quick' ? { routes: 1, max_iterations_per_route: 3 } : {}) } });
  };
  if (!spec) return error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取…')}</p>;
  return <div className="research-layout">
    <div className="card form">
      <div className="section-head"><h2>{t('自主研究 · 研究约定')}</h2><button type="button" disabled={busy} onClick={switchMode}>{t(mode === 'form' ? '直接编辑 YAML' : '返回表单')}</button></div>
      {error && <ErrorNote error={error} />}{notice && <span className="small muted" role="status">{notice}</span>}
      <div className="form-row"><label>{t('模板')}<select value={chosen} onChange={(e) => { const found = templates.find((x) => x.name === e.target.value); if (found) pick(found); }}>{templates.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}</select></label>
        <label>{t('运行 id')}<input className="mono" value={runId} onChange={(e) => setRunId(e.target.value)} /></label></div>
      <ExecutionEnvironmentPicker selection={execution} disabled={busy} />
      {mode === 'yaml' ? <label>{t('研究约定（YAML）')}<textarea className="mono" rows={32} value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} /></label> : <>
        <label>{t('研究标题')}<input value={spec.title ?? ''} onChange={(e) => patch({ title: e.target.value })} /></label>
        <label>{t('研究目标')}<textarea rows={5} value={spec.objective ?? ''} onChange={(e) => patch({ objective: e.target.value })} /></label>
        <h3>{t('目标与验收门槛')}<Hint text="门槛由程序根据实测结果判定，不交给模型。" /></h3>
        <GatesEditor targets={spec.targets} onChange={(targets) => patch({ targets })} />
        <h3>{t('约束')}</h3>
        {(spec.constraints ?? []).map((value, i) => <div className="path-bar" key={i}><textarea rows={2} value={value} aria-label={t('约束 {n}', { n: i + 1 })} onChange={(e) => patch({ constraints: spec.constraints?.map((s, n) => n === i ? e.target.value : s) })} /><button type="button" aria-label={t('移除约束')} onClick={() => patch({ constraints: spec.constraints?.filter((_, n) => n !== i) })}>×</button></div>)}
        <button type="button" onClick={() => patch({ constraints: [...(spec.constraints ?? []), ''] })}>+ {t('添加约束')}</button>
        <h3>{t('预算')}</h3><div className="pills" role="group" aria-label={t('预算')}>{[['quick', '快速试探'], ['standard', '标准'], ['custom', '自定义']].map(([key, label]) => <button type="button" key={key} aria-pressed={preset === key} className={preset === key ? 'on' : ''} onClick={() => applyPreset(key)}>{t(label)}</button>)}</div>
        <div className="target-grid">{BUDGETS.map(([key, label]) => <label key={key}>{t(label)}<input type="number" min={1} step={1} value={Number(spec.budget?.[key] ?? 1)} onChange={(e) => { patch({ budget: { ...spec.budget, [key]: Number(e.target.value) } }); setPreset('custom'); }} /></label>)}</div>
      </>}
      <label className="inline"><input type="checkbox" checked={humanApproval} onChange={(e) => setHumanApproval(e.target.checked)} />{t('Step1 结束后由我确认进入 Step2 的方案')}<Hint text="每次回到 Step1 都会再停一次；关闭后按评审排序自动选择。" /></label>
      <div className="wizard-footer"><a className="button" href="#/">{t('取消')}</a><div className="actions"><button type="button" disabled={busy || !runId.trim()} onClick={saveDraft}>{t('保存研究约定')}</button><button type="button" className="primary" disabled={busy || !execution.ready || !runId.trim() || !current?.connected} onClick={submit}>{busy ? t('正在检查…') : t('检查并启动')}</button></div></div>
    </div>
    <aside className="card launch-explainer"><h2>Model RSI</h2>
      <RsiFlow kind="rsi" humanApproval={humanApproval} /></aside>
  </div>;
}

/** Gates on the project's declared metrics (full verification against the baseline) and on its checks' outputs. */
function GatesEditor({ targets, onChange }: { targets: Targets | undefined; onChange: (targets: Targets) => void }) {
  const t = useT();
  const [decl, setDecl] = useState<Declaration | null>(null);
  useEffect(() => { api.declaration().then(setDecl, () => setDecl(null)); }, []);
  const gates = targets?.gates ?? [];
  const checks = targets?.checks ?? [];
  const metrics = decl?.metrics ?? { primary: null, fields: {} };
  const setGates = (next: MetricGate[]) => onChange({ ...targets, gates: next });
  const setChecks = (next: CheckGate[]) => onChange({ ...targets, checks: next });
  const unused = Object.keys(metrics.fields).filter((m) => !gates.some((g) => g.metric === m));
  return <>
    {decl && !decl.ready && <div className="note note-warn small">{t('项目声明还不完整：')}{decl.problems.join('；')}</div>}
    <div className="target-grid">
      {gates.map((g, i) => <div key={`${g.metric}-${i}`} className="path-bar">
        <MarginInput label={metricLabel(metrics, g.metric)} unit={metrics.fields[g.metric]?.unit} value={num(g.max_worse, 0)}
          onChange={(v) => setGates(gates.map((x, n) => (n === i ? { ...x, max_worse: v } : x)))} />
        <button type="button" aria-label={t('移除门槛')} onClick={() => setGates(gates.filter((_, n) => n !== i))}>×</button>
      </div>)}
    </div>
    {unused.length > 0 && <label>{t('添加验收指标')}<select value="" onChange={(e) => e.target.value && setGates([...gates, { metric: e.target.value, max_worse: 0 }])}>
      <option value="">—</option>{unused.map((m) => <option key={m} value={m}>{metricLabel(metrics, m)}</option>)}</select></label>}
    <h3>{t('检查门槛')}<Hint text="对项目检查输出的字段设上下限；字段路径可用 * 匹配所有键。勾选“不满足不训练”时，该门槛不通过就不进入训练。" /></h3>
    {checks.map((g, i) => {
      const update = (patch: Partial<CheckGate>) => setChecks(checks.map((x, n) => (n === i ? { ...x, ...patch } : x)));
      const bound = g.max !== undefined ? 'max' : 'min';
      return <div key={i} className="path-bar">
        <select value={g.check} aria-label={t('检查')} onChange={(e) => update({ check: e.target.value })}>
          {[...new Set([g.check, ...(decl?.checks ?? [])])].map((c) => <option key={c} value={c}>{c}</option>)}</select>
        <input className="mono" value={g.field} aria-label={t('字段')} onChange={(e) => update({ field: e.target.value })} />
        <select value={bound} aria-label={t('上限或下限')} onChange={(e) => { const value = g[bound]; update(e.target.value === 'max' ? { max: value, min: undefined } : { min: value, max: undefined }); }}>
          <option value="max">≤</option><option value="min">≥</option></select>
        <input type="number" step="any" value={num(g[bound], 0)} aria-label={t('数值')} onChange={(e) => update({ [bound]: Number(e.target.value) })} />
        <label className="inline"><input type="checkbox" checked={!!g.stop} onChange={(e) => update({ stop: e.target.checked })} />{t('不满足不训练')}</label>
        <button type="button" aria-label={t('移除门槛')} onClick={() => setChecks(checks.filter((_, n) => n !== i))}>×</button>
      </div>;
    })}
    {(decl?.checks.length ?? 0) > 0 && <button type="button" onClick={() => setChecks([...checks, { check: decl!.checks[0], field: '', max: 1 }])}>+ {t('添加检查门槛')}</button>}
  </>;
}
