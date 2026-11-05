import { useEffect, useMemo, useState } from 'react';
import { api, type HpoDetail, type HpoMeta, type HpoParam, type HpoSpec, type HpoState, type HpoSummary, type SearchKey } from '../api';
import { ErrorNote, Hint, datasetNames, datasetOrder, num } from '../components/ui';
import { ExecutionEnvironmentPicker, useExecutionEnvironment } from '../components/ExecutionEnvironment';
import { useT } from '../prefs';
import { useProjects } from '../projects';
import { useConfirm } from '../components/Confirm';
import { StepsInput } from '../components/StepsInput';

const STATE_LABEL: Record<HpoState, string> = { running: '进行中', stopping: '正在停止', paused: '已暂停', finished: '已完成' };
const STATE_CLASS: Record<HpoState, string> = { running: 'state-running', stopping: 'state-waiting_approval', paused: '', finished: 'state-finished' };
const TRIAL_LABEL: Record<string, string> = { complete: '完成', fail: '失败', running: '训练中', interrupted: '中断', waiting: '排队', pruned: '剪枝' };

function HpoBadge({ state }: { state: HpoState }) {
  const t = useT();
  return <span className={`badge ${STATE_CLASS[state]}`}>{t(STATE_LABEL[state])}</span>;
}

function value(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : Math.abs(v) < 1e-3 || Math.abs(v) >= 1e4 ? v.toExponential(2) : String(+v.toPrecision(4));
  return String(v);
}

function delta(v: number | null | undefined): string {
  return typeof v === 'number' ? `${v > 0 ? '+' : ''}${+v.toPrecision(4)}` : '—';
}

/** The study's objective: the project's primary metric and its direction. */
const objective = (spec: HpoSpec) => `${spec.metric ?? '—'}（${spec.direction === 'maximize' ? '越高越好' : '越低越好'}）`;

function paramsText(params: Record<string, unknown>): string {
  return Object.entries(params).map(([k, v]) => `${k}=${value(v)}`).join('  ');
}

// ---------- list ----------
export function HpoList() {
  const t = useT();
  const demo = Boolean(useProjects().current?.demo);
  const [items, setItems] = useState<HpoSummary[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const load = () => api.hpoList().then((x) => alive && setItems(x), (e: Error) => alive && setError(e.message));
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  return (
    <section>
      <div className="page-head">
        <h1>{t('超参寻优')}<Hint text="在固定的基线或某个候选上只改训练配置，用 Optuna 搜索；每次试验调用内核评测，结果按配置缓存。" /></h1>
        {!demo && <a className="button primary" href="#/hpo/new">{t('新建寻优')}</a>}
      </div>
      {error && <ErrorNote error={error} />}
      {items === null && !error && <p className="muted">{t('正在读取…')}</p>}
      {items?.length === 0 && !demo && <div className="card empty-state compact"><a className="button primary" href="#/hpo/new">{t('新建寻优')}</a></div>}
      {items && items.length > 0 && (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr><th>{t('寻优')}</th><th>{t('对象')}</th><th>{t('状态')}</th><th>{t('进度')}</th><th>{t('最好目标值')}</th><th>{t('相对默认配置的改善')}</th><th>{t('最好参数')}</th></tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id} className="clickable" onClick={() => (window.location.hash = `#/hpo/${s.id}`)}>
                  <td><strong>{s.spec.title || s.id}</strong><div className="muted small mono">{s.id}</div></td>
                  <td>{s.spec.target.label || s.spec.target.kind}<div className="muted small">{datasetNames(s.spec.datasets)} · {s.spec.steps ? t('{n} 步', { n: s.spec.steps }) : t('完整预算')}</div></td>
                  <td><HpoBadge state={s.state} /></td>
                  <td className="num">{s.counts.complete + s.counts.fail} / {s.spec.n_trials}{s.counts.fail > 0 && <span className="muted">{t('（失败 {n}）', { n: s.counts.fail })}</span>}</td>
                  <td className="num">{s.best ? num(s.best.value, 4) : '—'}</td>
                  <td className="num">{delta(s.best_gain)}</td>
                  <td className="mono small">{s.best ? paramsText(s.best.params) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------- new study ----------
type Row = { key: string; mode: 'range' | 'choices'; low: string; high: string; log: boolean; choices: string };

function defaultRow(info: SearchKey): Row {
  if ((info.type === 'float' || info.type === 'int') && typeof info.low === 'number' && typeof info.high === 'number') {
    return { key: info.key, mode: 'range', low: value(info.low), high: value(info.high), log: Boolean(info.log), choices: '' };
  }
  return { key: info.key, mode: 'choices', low: '', high: '', log: false, choices: (info.choices ?? []).map(String).join(', ') };
}

function toParam(row: Row): HpoParam {
  return row.mode === 'range'
    ? { key: row.key, mode: 'range', low: row.low, high: row.high, log: row.log }
    : { key: row.key, mode: 'choices', choices: row.choices.split(/[,，]/).map((s) => s.trim()).filter(Boolean) };
}

export function HpoNew() {
  const confirm = useConfirm();
  const t = useT();
  const execution = useExecutionEnvironment();
  const [meta, setMeta] = useState<HpoMeta | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState('');
  const [target, setTarget] = useState('baseline');
  const [datasets, setDatasets] = useState<string[]>([]);
  const [steps, setSteps] = useState(0);
  const [seed, setSeed] = useState(2026);
  const [nTrials, setNTrials] = useState(20);
  const [parallel, setParallel] = useState(2);
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    api.hpoMeta().then((m) => {
      setMeta(m);
      setDatasets(m.search_space.datasets);  // whatever the project binds
      const first = m.search_space.keys.find((k) => k.declared);
      if (first) setRows([defaultRow(first)]);
    }, (e: Error) => setError(e.message));
  }, []);

  const keys = useMemo(() => (meta?.search_space.keys ?? []).filter((k) => k.declared && ['float', 'int', 'str', 'bool', 'categorical'].includes(k.type ?? '')), [meta]);
  const info = (key: string) => keys.find((k) => k.key === key);
  const candidate = meta?.candidates.find((c) => c.dir === target);
  const evaluations = (nTrials + 1) * datasets.length;

  if (!meta) return error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取搜索空间…')}</p>;

  const update = (index: number, change: Partial<Row>) => setRows((rs) => rs.map((r, i) => (i === index ? { ...r, ...change } : r)));
  const unused = keys.filter((k) => !rows.some((r) => r.key === k.key));

  const submit = async () => {
    const label = target === 'baseline' ? t('基线') : candidate ? `${candidate.run_id} · ${candidate.route} ${t('第 {k} 轮', { k: candidate.iteration })}` : target;
    const message = t('启动寻优：{e} 次评测（{n} 次试验 + 1 次默认配置参照，各跑 {d}，每次 {s}）。', { e: evaluations, n: nTrials, d: datasetNames(datasets), s: steps ? t('{n} 步', { n: steps }) : t('完整预算') }) + '\n' +
      t('评测在所选环境中真实训练；已经跑过的配置会直接读缓存。');
    if (!(await confirm(message))) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.hpoCreate({
        title: title || `${label} · ${rows.map((r) => r.key).join(', ')}`,
        target: { kind: target === 'baseline' ? 'baseline' : 'candidate', dir: target === 'baseline' ? meta.baseline_dir : target, label },
        datasets, steps, seed, n_trials: nTrials, parallel, params: rows.map(toParam), environment: execution.environment,
      });
      window.location.hash = `#/hpo/${result.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="page-head"><h1>{t('新建寻优')}<Hint text="结构不变，只搜索项目声明的可调配置。目标：所选数据集上项目主指标 {m} 的平均值。" params={{ m: meta ? `${meta.search_space.metrics.primary ?? '—'}（${meta.search_space.metrics.fields[meta.search_space.metrics.primary ?? '']?.better === 'higher' ? '越高越好' : '越低越好'}）` : '' }} /></h1></div>
      {error && <ErrorNote error={error} />}
      <div className="card form">
        <ExecutionEnvironmentPicker selection={execution} disabled={busy} />
        <label>
          {t('名称')}
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('留空则按对象和参数自动命名')} />
        </label>
        <label>
          {t('评测对象')}
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="baseline">{t('基线')}</option>
            {Object.entries(byRun(meta.candidates)).map(([run, items]) => <optgroup key={run} label={run}>
              {items.map((c) => <option key={c.dir} value={c.dir} title={c.change}>{c.route} · {t('第 {k} 轮', { k: c.iteration })} · {short(c.change, 24)}</option>)}
            </optgroup>)}
          </select>
        </label>
        <div className="form-row">
          <fieldset>
            <legend>{t('数据集')}</legend>
            {[...meta.search_space.datasets].sort(datasetOrder).map((d) => (
              <label key={d} className="inline">
                <input type="checkbox" checked={datasets.includes(d)}
                  onChange={(e) => setDatasets((ds) => (e.target.checked ? [...ds, d] : ds.filter((x) => x !== d)))} />
                {d}
              </label>
            ))}
          </fieldset>
          <StepsInput steps={steps} onChange={setSteps} candidateDir={target === 'baseline' ? null : target}
            hint="按默认配置折算；试验改了影响每 epoch 步数的配置时，实际步数会随之变化。" />
          <label>
            {t('种子')}
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          </label>
          <label>
            {t('试验次数')}
            <input type="number" min={1} max={meta.max_trials} value={nTrials} onChange={(e) => setNTrials(Number(e.target.value))} />
          </label>
          <label>
            <span>{t('同时运行')}<Hint text="所有寻优合计最多 {n} 个" params={{ n: meta.max_parallel }} /></span>
            <select value={parallel} onChange={(e) => setParallel(Number(e.target.value))}>
              {Array.from({ length: meta.max_parallel }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>
        <div>
          <div className="run-card-head"><strong>{t('搜索参数')}</strong></div>
          <div className="param-rows">
            {rows.map((row, i) => {
              const k = info(row.key);
              const numeric = k?.type === 'float' || k?.type === 'int';
              return (
                <div key={row.key} className="param-row">
                  <select value={row.key} onChange={(e) => { const next = info(e.target.value); if (next) update(i, defaultRow(next)); }}>
                    {[k, ...unused].filter((x): x is SearchKey => Boolean(x)).map((x) => <option key={x.key} value={x.key}>{x.key}</option>)}
                  </select>
                  {numeric && (
                    <select value={row.mode} onChange={(e) => update(i, { mode: e.target.value as Row['mode'] })}>
                      <option value="range">{t('范围')}</option>
                      <option value="choices">{t('候选值')}</option>
                    </select>
                  )}
                  {row.mode === 'range' ? (
                    <>
                      <input className="mono short" value={row.low} onChange={(e) => update(i, { low: e.target.value })} aria-label={t('下限')} />
                      <span className="muted">{t('至')}</span>
                      <input className="mono short" value={row.high} onChange={(e) => update(i, { high: e.target.value })} aria-label={t('上限')} />
                      <label className="inline"><input type="checkbox" checked={row.log} onChange={(e) => update(i, { log: e.target.checked })} />{t('对数')}</label>
                    </>
                  ) : (
                    <input className="mono" value={row.choices} onChange={(e) => update(i, { choices: e.target.value })} placeholder={t('逗号分隔')} aria-label={t('候选值')} />
                  )}
                  <span className="muted small">{k && k.type ? `${k.type}${k.low !== undefined ? ` ${value(k.low)}–${value(k.high)}` : ''}` : ''}</span>
                  <button onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} aria-label={t('删除')}>{t('删除')}</button>
                </div>
              );
            })}
          </div>
          {unused.length > 0 && (
            <button onClick={() => setRows((rs) => [...rs, defaultRow(unused[0])])}>{t('添加参数')}</button>
          )}
        </div>
        <div className="approval-foot">
          <span className="tag">{t('{n} 次评测', { n: evaluations })}<Hint text="含 1 次默认配置参照；已跑过的配置直接读缓存。" /></span>
          <button className="primary" disabled={busy || !execution.ready || rows.length === 0 || datasets.length === 0} onClick={submit}>
            {busy ? t('正在启动…') : t('启动寻优')}
          </button>
        </div>
      </div>
    </section>
  );
}

const short = (text: string, n: number) => (text.length > n ? `${text.slice(0, n)}…` : text);
function byRun<T extends { run_id: string }>(items: T[]): Record<string, T[]> {
  return items.reduce<Record<string, T[]>>((groups, item) => ((groups[item.run_id] ??= []).push(item), groups), {});
}

/** The next step after a search: the best settings as config lines, ready to paste into a training config. */
function CopyConfig({ params }: { params: Record<string, unknown> }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const text = Object.entries(params).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n');
  return <button type="button" onClick={() => {
    void navigator.clipboard?.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 2000); });
  }}>{copied ? t('已复制') : t('复制为配置')}</button>;
}

// ---------- study detail ----------
export function HpoView({ id }: { id: string }) {
  const confirm = useConfirm();
  const t = useT();
  const demo = Boolean(useProjects().current?.demo);
  const [study, setStudy] = useState<HpoDetail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.hpo(id).then((s) => alive && (setStudy(s), setError('')), (e: Error) => alive && setError(e.message));
    load();
    const timer = window.setInterval(load, 10000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [id]);

  if (!study) return error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取…')}</p>;
  const { spec } = study;
  const reference = study.reference?.value ?? null;
  const done = study.counts.complete + study.counts.fail;

  const control = async (action: 'stop' | 'resume') => {
    const prompt = action === 'stop'
      ? t('停止寻优？已经开始训练的试验会跑完，之后不再开始新的试验；可以随时继续。')
      : t('继续寻优？会接着跑完剩余的试验次数。');
    if (!(await confirm(prompt))) return;
    setBusy(true);
    try {
      await api.hpoControl(id, action);
      setStudy(await api.hpo(id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <a className="back" href="#/hpo">{t('← 全部寻优')}</a>
      <div className="page-head">
        <div>
          <h1>{spec.title || id}</h1>
          <div className="wb-meta">
            <HpoBadge state={study.state} />
            <span>{spec.target.label}</span>
            <span>{datasetNames(spec.datasets)}</span>
            <span>{spec.steps ? t('{n} 步', { n: spec.steps }) : t('完整预算')}</span>
            <span>{t('种子')} {spec.seed}</span>
            {spec.environment && <span>{t(spec.environment === 'local' ? '本地' : '远程 GPU')}</span>}
          </div>
        </div>
        <div className="actions">
          {study.state === 'running' && !demo && <button disabled={busy} onClick={() => control('stop')}>{t('停止')}</button>}
          {study.state === 'paused' && !demo && <button className="primary" disabled={busy} onClick={() => control('resume')}>{t('继续')}</button>}
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      {study.state === 'paused' && study.counts.interrupted > 0 && (
        <div className="alert alert-warn">{t('有 {n} 次试验因应用重启被中断，不计入次数；点“继续”会补跑。', { n: study.counts.interrupted })}</div>
      )}
      <div className="stat-grid">
        <div className="card stat">
          <span className="muted small">{t('进度')}</span>
          <strong>{done} / {spec.n_trials}</strong>
          <span className="muted small">{t('完成 {c} · 失败 {f} · 训练中 {r}', { c: study.counts.complete, f: study.counts.fail, r: study.counts.running })}</span>
        </div>
        <div className="card stat">
          <span className="muted small">{t('默认配置参照')}</span>
          <strong>{reference !== null ? num(reference, 4) : '—'}</strong>
          <span className="muted small">{objective(spec)}</span>
          {study.reference?.error && <span className="small tone-bad-text">{t('参照评测失败：{e}', { e: study.reference.error })}</span>}
        </div>
        <div className="card stat">
          <span className="muted small">{t('目前最好')}</span>
          <strong>{study.best ? num(study.best.value, 4) : '—'}</strong>
          <span className={`small ${study.best_gain !== null && study.best_gain > 0 ? 'tone-good-text' : 'muted'}`}>
            {study.best ? t('相对参照改善 {d} · 第 {n} 次', { d: delta(study.best_gain), n: study.best.number + 1 }) : t('尚无完成的试验')}
            {study.best_gain !== null && study.best_gain <= 0 && t('（还没有试验优于默认配置）')}
          </span>
        </div>
      </div>
      {study.best && (
        <div className="card">
          <div className="section-head"><h2>{t('最好参数')}</h2><CopyConfig params={study.best.params} /></div>
          <p className="mono">{paramsText(study.best.params)}</p>
        </div>
      )}
      <div className="card table-wrap">
        <h2>{t('试验')}</h2>
        <table>
          <thead>
            <tr>
              <th>#</th><th>{t('状态')}</th><th>{t('参数')}</th><th>{t('目标值（均值）')}</th><th>{t('相对参照')}</th>
              {spec.datasets.length > 1 && [...spec.datasets].sort(datasetOrder).map((d) => <th key={d}>{datasetNames([d])} {spec.metric}</th>)}
              <th>{t('耗时')}</th><th>{t('执行于')}</th>
            </tr>
          </thead>
          <tbody>
            {[...study.trials].reverse().map((trial) => {
              const first = Object.values(trial.datasets)[0];
              return (
                <tr key={trial.number} className={study.best?.number === trial.number ? 'best-row' : ''}>
                  <td className="num">{trial.number + 1}</td>
                  <td>{TRIAL_LABEL[trial.state] ? t(TRIAL_LABEL[trial.state]) : trial.state}{trial.error && <div className="tone-bad-text small">{trial.error}</div>}</td>
                  <td className="mono small">{paramsText(trial.params)}</td>
                  <td className="num">{trial.value !== null ? num(trial.value, 4) : '—'}</td>
                  <td className="num">{trial.value !== null && reference !== null ? delta(trial.value - reference) : '—'}</td>
                  {spec.datasets.length > 1 && [...spec.datasets].sort(datasetOrder).map((d) => <td key={d} className="num">{num(trial.datasets[d]?.metrics?.[spec.metric ?? ''], 4)}</td>)}
                  <td className="num">{trial.seconds !== null ? t('{m} 分', { m: Math.round(trial.seconds / 60) }) : '—'}</td>
                  <td className="small">{first?.executed_on ?? '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
