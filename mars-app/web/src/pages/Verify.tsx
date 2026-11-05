import { useEffect, useState } from 'react';
import { api, type MetricsDecl, type VerifyComparisonRow, type VerifyDetail, type VerifyMeta, type VerifyStage, type VerifySummary } from '../api';
import { ErrorNote, Hint, datasetNames, datasetOrder, num } from '../components/ui';
import { ExecutionEnvironmentPicker, useExecutionEnvironment } from '../components/ExecutionEnvironment';
import { useT } from '../prefs';
import { useProjects } from '../projects';
import { useConfirm } from '../components/Confirm';
import { StepsInput } from '../components/StepsInput';
import { missingModels } from '../modelStatus';
import { checkLines, metricLabel } from '../metrics';

const STAGE_LABEL: Record<VerifyStage, string> = {
  implementing: '实现中', evaluating: '训练评测中', done: '已完成', failed: '失败', interrupted: '已中断',
};
const STAGE_CLASS: Record<VerifyStage, string> = {
  implementing: 'state-running', evaluating: 'state-running', done: 'state-finished', failed: 'tone-bad', interrupted: 'state-waiting_approval',
};
const EXAMPLE = '描述要实现的改动：改哪一部分、怎么改、初始化方式，其余保持不变。';

function StageBadge({ stage }: { stage: VerifyStage }) {
  const t = useT();
  return <span className={`badge ${STAGE_CLASS[stage]}`}>{t(STAGE_LABEL[stage])}</span>;
}

function Verdict({ ok }: { ok: boolean | null | undefined }) {
  const t = useT();
  if (ok === true) return <span className="tone-good-text">{t('达标')}</span>;
  if (ok === false) return <span className="tone-bad-text">{t('未达标')}</span>;
  return <span className="muted">—</span>;
}

function signed(v: number | null | undefined, digits = 3): string {
  return typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(digits)}` : '—';
}

// ---------- list ----------
export function VerifyList() {
  const t = useT();
  const demo = Boolean(useProjects().current?.demo);
  const [items, setItems] = useState<VerifySummary[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const load = () => api.verifyList().then((x) => alive && setItems(x), (e: Error) => alive && setError(e.message));
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
        <h1>{t('仿真验证')}<Hint text="给定一个成熟方案，由编码 Agent 实现成候选，再和基线在同样的预算下训练对比。" /></h1>
        {!demo && <a className="button primary" href="#/verify/new">{t('新建验证')}</a>}
      </div>
      {error && <ErrorNote error={error} />}
      {items === null && !error && <p className="muted">{t('正在读取…')}</p>}
      {items?.length === 0 && !demo && <div className="card empty-state compact"><a className="button primary" href="#/verify/new">{t('新建验证')}</a></div>}
      {items && items.length > 0 && (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>{t('方案')}</th><th>{t('状态')}</th><th>{t('数据 · 预算')}</th><th>{t('结论')}</th><th>{t('创建')}</th></tr></thead>
            <tbody>
              {items.map((v) => (
                <tr key={v.id} className="clickable" onClick={() => (window.location.hash = `#/verify/${v.id}`)}>
                  <td><strong>{v.title}</strong><div className="muted small mono">{v.run_id}</div>{v.error && <div className="tone-bad-text small">{v.error}</div>}</td>
                  <td><StageBadge stage={v.stage} /></td>
                  <td className="small">{datasetNames(v.datasets)} · {v.steps ? t('{n} 步', { n: v.steps }) : t('完整预算')}</td>
                  <td>{v.stage === 'done' ? <Verdict ok={v.all_ok} /> : <span className="muted">—</span>}</td>
                  <td className="small muted">{v.created}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------- new ----------
export function VerifyNew() {
  const confirm = useConfirm();
  const t = useT();
  const execution = useExecutionEnvironment();
  const [meta, setMeta] = useState<VerifyMeta | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState('');
  const [template, setTemplate] = useState('');
  const [spec, setSpec] = useState('');
  const [parent, setParent] = useState('mars/base');
  const [datasets, setDatasets] = useState<string[]>([]);
  const [steps, setSteps] = useState(0);
  const [seed, setSeed] = useState(2026);

  useEffect(() => {
    api.verifyMeta().then((m) => {
      setMeta(m);
      setTemplate(m.templates[0]?.name ?? '');
      setDatasets(m.datasets);
    }, (e: Error) => setError(e.message));
  }, []);

  if (!meta) return error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取…')}</p>;

  const submit = async () => {
    try {
      const missing = await missingModels();
      if (missing.length) {
        setError(t('模型还没配置好，启动后会立刻失败：{p}。请先到“设置 → 模型连接”补全。', { p: missing.map((m) => t(m.text, m.params)).join('；') }));
        return;
      }
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    const message = t('开始仿真验证：编码 Agent 实现方案（会调用模型，通常 10–40 分钟），然后在 {d} 上各训练候选和基线一次（每次 {s}，基线有缓存时直接读取）。', { d: datasetNames(datasets), s: steps ? t('{n} 步', { n: steps }) : t('完整预算') });
    if (!(await confirm(message))) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.verifyCreate({ title, template, spec, datasets, steps, seed, parent, environment: execution.environment });
      window.location.hash = `#/verify/${result.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="page-head"><h1>{t('新建仿真验证')}</h1></div>
      {error && <ErrorNote error={error} />}
      <div className="card form">
        <ExecutionEnvironmentPicker selection={execution} disabled={busy} />
        <label>
          <span>{t('方案描述')}<Hint text="写清要改哪一层、改成什么、怎么初始化、哪些部分保持不变。编码 Agent 只能改候选模型文件和允许的训练配置键。" /></span>
          <textarea rows={8} value={spec} onChange={(e) => setSpec(e.target.value)} placeholder={t('例如：{x}', { x: t(EXAMPLE) })} />
        </label>
        <label>
          {t('名称')}
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('留空则取方案描述第一行')} />
        </label>
        <div className="form-row">
          <label>
            <span>{t('研究约定')}<Hint text="用其中的指标门槛和检查门槛判断是否达标" /></span>
            <select value={template} onChange={(e) => setTemplate(e.target.value)}>
              {meta.templates.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
            </select>
          </label>
          <label>
            {t('在哪个版本上实现')}
            <select value={parent} onChange={(e) => setParent(e.target.value)}>
              <option value="mars/base">{t('基线')}</option>
              {Object.entries(groupBy(meta.parents, (p) => p.run_id ?? '')).map(([run, items]) => <optgroup key={run} label={run}>
                {items.map((p) => <option key={p.branch} value={p.branch}>{p.route} · {t('第 {k} 轮', { k: p.iteration })} · {short(p.change, 24)}</option>)}
              </optgroup>)}
            </select>
          </label>
        </div>
        <div className="form-row">
          <fieldset>
            <legend>{t('数据集')}</legend>
            {[...meta.datasets].sort(datasetOrder).map((d) => (
              <label key={d} className="inline">
                <input type="checkbox" checked={datasets.includes(d)}
                  onChange={(e) => setDatasets((ds) => (e.target.checked ? [...ds, d] : ds.filter((x) => x !== d)))} />
                {d}
              </label>
            ))}
          </fieldset>
          <StepsInput steps={steps} onChange={setSteps} hint="按基线折算；实现后的候选若改了切窗或 batch，会略有不同。" />
          <label>
            {t('种子')}
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          </label>
        </div>
        <div className="approval-foot">
          <span />
          <button className="primary" disabled={busy || !execution.ready || !spec.trim() || datasets.length === 0 || !template} onClick={submit}>
            {busy ? t('正在提交…') : t('开始验证')}
          </button>
        </div>
      </div>
    </section>
  );
}

const short = (text: string, n: number) => (text.length > n ? `${text.slice(0, n)}…` : text);
function groupBy<T>(items: T[], key: (item: T) => string): Record<string, T[]> {
  return items.reduce<Record<string, T[]>>((groups, item) => ((groups[key(item)] ??= []).push(item), groups), {});
}

/** One dataset at a time; its images stay folded until asked for, candidate and baseline side by side. */
function DatasetComparison({ comparison, metrics }: { comparison: Record<string, VerifyComparisonRow>; metrics: MetricsDecl }) {
  const t = useT();
  const names = Object.keys(comparison).sort(datasetOrder);
  const [picked, setPicked] = useState<string | null>(null);
  if (!names.length) return null;
  const dataset = picked && names.includes(picked) ? picked : names[0];
  const row = comparison[dataset];
  const images = Math.max(row.plots.candidate.length, row.plots.baseline.length);
  return <div className="card stack" style={{ gap: 10 }}>
    {names.length > 1 && <div className="seg" role="group" aria-label={t('数据集')} style={{ alignSelf: 'flex-start' }}>
      {names.map((d) => <button key={d} type="button" className={d === dataset ? 'on' : ''} onClick={() => setPicked(d)}>{datasetNames([d])}</button>)}
    </div>}
    <div className="table-wrap"><ComparisonTable row={row} metrics={metrics} /></div>
    {images > 0 && <details key={dataset}>
      <summary>{t('评测图')}</summary>
      {Array.from({ length: images }, (_, i) => <div key={i} className="plots side-by-side">
        {(['candidate', 'baseline'] as const).map((role) => row.plots[role][i] && <figure key={role}>
          <img src={api.plotUrl(row.plots[role][i])} alt={t(role === 'candidate' ? '{d} 候选' : '{d} 基线', { d: dataset })} loading="lazy" />
          <figcaption className="muted small">{role === 'candidate' ? t('候选') : t('基线')}</figcaption>
        </figure>)}
      </div>)}
    </details>}
  </div>;
}

// ---------- detail ----------
function ComparisonTable({ row, metrics }: { row: VerifyComparisonRow; metrics: MetricsDecl }) {
  const t = useT();
  const names = Object.keys(metrics.fields).filter((n) => row.metrics[n]);
  return (
    <table>
      <thead><tr><th>{t('指标')}</th><th>{t('候选')}</th><th>{t('基线')}</th><th>{t('差于基线')}</th><th>{t('门槛')}</th></tr></thead>
      <tbody>
        {names.map((n) => {
          const c = row.metrics[n];
          return (
            <tr key={n}>
              <td>{metricLabel(metrics, n)}</td>
              <td className="num">{num(c.candidate, 4)}</td>
              <td className="num">{num(c.baseline, 4)}</td>
              <td className={`num ${typeof c.worse_by === 'number' ? (c.worse_by <= 0 ? 'tone-good-text' : 'tone-bad-text') : ''}`}>{signed(c.worse_by, 4)}</td>
              <td>{c.max_worse !== null ? <Verdict ok={c.ok} /> : <span className="muted small">{t('参考')}</span>}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function VerifyView({ id }: { id: string }) {
  const confirm = useConfirm();
  const t = useT();
  const demo = Boolean(useProjects().current?.demo);
  const [job, setJob] = useState<VerifyDetail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.verify(id).then((j) => alive && (setJob(j), setError('')), (e: Error) => alive && setError(e.message));
    load();
    const timer = window.setInterval(load, 10000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [id]);

  if (!job) return error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取…')}</p>;
  const impl = job.implementation;
  const cmp = job.comparison;

  const control = async (action: 'stop' | 'resume') => {
    const prompt = action === 'stop'
      ? t('停止验证？实现阶段会结束编码 Agent；评测阶段已经开始的训练会跑完，之后不再开始新的评测。可以随时继续。')
      : t('继续验证？没完成的实现会重新开始，已完成的评测直接读缓存。');
    if (!(await confirm(prompt))) return;
    setBusy(true);
    try {
      await api.verifyControl(id, action);
      setJob(await api.verify(id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const steps: [string, boolean, boolean][] = [
    ['实现', job.stage === 'implementing', impl !== null],
    ['训练评测', job.stage === 'evaluating', job.stage === 'done'],
    ['对比', false, job.stage === 'done'],
  ];

  return (
    <section>
      <a className="back" href="#/verify">{t('← 全部验证')}</a>
      <div className="page-head">
        <div>
          <h1>{job.title}</h1>
          <div className="wb-meta">
            <StageBadge stage={job.stage} />
            <span>{datasetNames(job.datasets)}</span>
            <span>{job.steps ? t('{n} 步', { n: job.steps }) : t('完整预算')}</span>
            <span>{t('种子')} {job.seed}</span>
            {job.environment && <span>{t(job.environment === 'local' ? '本地' : '远程 GPU')}</span>}
            <span>{job.parent === 'mars/base' ? t('基于基线') : <span className="mono">{job.parent}</span>}</span>
            <a className="mono" href={`#/runs/${encodeURIComponent(job.run_id)}`}>{job.run_id}</a>
          </div>
        </div>
        <div className="actions">
          {job.running && !demo && <button disabled={busy} onClick={() => control('stop')}>{t('停止')}</button>}
          {!job.running && !demo && (job.stage === 'interrupted' || job.stage === 'failed') && (
            <button className="primary" disabled={busy} onClick={() => control('resume')}>{t('继续')}</button>
          )}
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      {job.error && <div className={`alert ${job.stage === 'interrupted' ? 'alert-warn' : 'alert-error'}`}>{job.error}</div>}
      <ol className="stages">
        {steps.map(([label, active, done]) => (
          <li key={label} className={done ? 'done' : active ? 'active' : ''}>{t(label)}{active && job.running ? '…' : ''}</li>
        ))}
      </ol>

      {job.stage === 'done' && (
        <div className={`alert ${cmp.all_ok ? 'alert-info' : 'alert-warn'}`}>
          <strong>{cmp.all_ok ? t('所有门槛都满足') : t('有门槛未满足')}</strong>
          <span className="tag" style={{ marginLeft: 8 }}>{cmp.full_budget ? t('完整预算') : t('短预算参考')}</span>
          <Hint text={cmp.full_budget ? '完整预算单种子结果：与内核的正式验收口径一致，跨种子稳定性需另行确认。'
            : '{n} 步筛选预算下的参考对比：短预算排名可能在完整训练后翻转，正式结论请用完整预算（步数 0）再验证。'} params={{ n: job.steps }} />
          {cmp.check_gates && 'all' in cmp.check_gates && cmp.check_gates.all === false && <div className="small">{t('检查门槛未满足，见实现详情。')}</div>}
        </div>
      )}

      <DatasetComparison comparison={cmp.datasets} metrics={cmp.metrics} />
      {job.stage === 'evaluating' && Object.keys(cmp.datasets).length === 0 && <p className="muted">{t('训练中…')}</p>}

      {impl && (
        <div className="card">
          <div className="run-card-head">
            <h2>{t('实现')}</h2>
            <span className={`badge ${impl.ok ? 'tone-good' : 'tone-bad'}`}>{impl.ok ? t('通过检查') : t('未通过检查')}</span>
          </div>
          <dl className="proposal-fields">
            <dt>{t('分支')}</dt><dd className="mono">{impl.branch ?? '—'} {impl.commit ? `@ ${impl.commit.slice(0, 10)}` : ''}</dd>
            <dt>{t('改动文件')}</dt><dd className="mono">{impl.changed_files?.join('\n') || '—'}</dd>
            <dt>{t('范围检查')}</dt><dd>{impl.violations?.length ? impl.violations.join('；') : t('通过')}</dd>
            <dt>{t('项目检查')}</dt>
            <dd>{Object.entries(impl.checks ?? {}).map(([name, r]) => <div key={name}>{name}：{r.ok === false ? t('未通过') : t('通过')}</div>)}{Object.keys(impl.checks ?? {}).length === 0 ? '—' : null}</dd>
            <dt>{t('检查门槛')}</dt>
            <dd>{checkLines(impl.gates).map((c) => <div key={c.text} className={c.ok ? '' : 'tone-bad-text'}>{c.text}</div>)}{checkLines(impl.gates).length === 0 ? '—' : null}</dd>
            <dt>{t('编码轮次')}</dt><dd>{impl.turns.map((x) => t('第 {n} 轮 {s}', { n: x.turn + 1, s: x.status }) + (x.seconds ? t('（{m} 分）', { m: Math.round(x.seconds / 60) }) : '')).join(' → ') || '—'}</dd>
          </dl>
          {impl.summary && (
            <details><summary>{t('编码 Agent 的总结')}</summary><pre className="code-block">{impl.summary}</pre></details>
          )}
        </div>
      )}
      <div className="card">
        <details>
          <summary>{t('方案描述')}</summary>
          <pre className="code-block">{job.spec}</pre>
        </details>
      </div>
    </section>
  );
}
