import { useEffect, useState } from 'react';
import { api, openStartedRun, type Template } from '../api';
import { ErrorNote, Hint } from '../components/ui';
import { ExecutionEnvironmentPicker, useExecutionEnvironment } from '../components/ExecutionEnvironment';
import { useT } from '../prefs';
import { useConfirm } from '../components/Confirm';
import { missingModels } from '../modelStatus';

const EXAMPLE = '想法：把基线中参数最多的部分换成更紧凑的结构，并从与基线等价的初始化开始训练；其余保持不变。';

function defaultId(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}-refine`;
}

/** Refine scenario: a rough design of the user's own is structured, prechecked and evolved nearby (no Step1). */
export function Refine() {
  const confirm = useConfirm();
  const t = useT();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [chosen, setChosen] = useState('');
  const [yaml, setYaml] = useState('');
  const [runId, setRunId] = useState(defaultId);
  const [proposal, setProposal] = useState('');
  const [humanApproval, setHumanApproval] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const execution = useExecutionEnvironment();

  useEffect(() => {
    api.templates().then((items) => {
      setTemplates(items);
      const preferred = items[0];
      if (preferred) {
        setChosen(preferred.name);
        setYaml(preferred.yaml);
      }
    }, (e: Error) => setError(e.message));
  }, []);

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
    const message = t(humanApproval
      ? '启动方案完善 {id}：先把你的方案结构化并做参数预检，停下来等你确认，然后在方案附近进化（会调用模型并在所选环境中训练，轮数按研究约定的 max_iterations_per_route）。'
      : '启动方案完善 {id}：先把你的方案结构化并做参数预检，然后在方案附近进化（会调用模型并在所选环境中训练，轮数按研究约定的 max_iterations_per_route）。', { id: runId });
    if (!(await confirm(message))) return;
    setBusy(true);
    setError('');
    try {
      const checked = await api.checkContract({ yaml, environment: execution.environment });
      if (checked.errors.length) throw new Error(checked.errors.join('；'));
      const result = await api.refine(runId, checked.yaml, proposal, humanApproval);
      await openStartedRun(result.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="page-head"><h1>{t('方案完善')}<Hint text="你已经有一个粗略的想法：系统把它结构化、预检参数量，然后在这个方案附近短预算进化，不重新调研和提案。" /></h1></div>
      {error && <ErrorNote error={error} />}
      <div className="card form">
        <ExecutionEnvironmentPicker selection={execution} disabled={busy} />
        <label>
          {t('你的方案')}
          <textarea rows={10} value={proposal} onChange={(e) => setProposal(e.target.value)} placeholder={t('例如：{x}', { x: t(EXAMPLE) })} />
        </label>
        <label className="switch">
          <input type="checkbox" checked={humanApproval} onChange={(e) => setHumanApproval(e.target.checked)} />
          <span>{t('结构化之后由我确认再开始进化')}<Hint text="运行会停在确认点，在运行页面查看结构化后的方案（U00）、预检和参数估计后再继续。" /></span>
        </label>
        <label>
          {t('运行 id')}
          <input className="mono" value={runId} onChange={(e) => setRunId(e.target.value)} spellCheck={false} />
        </label>
        <details>
          <summary>{t('研究约定（目标、约束与预算）')}</summary>
          <label>
            {t('模板')}
            <select value={chosen} onChange={(e) => { const found = templates.find((x) => x.name === e.target.value); if (found) { setChosen(found.name); setYaml(found.yaml); } }}>
              {templates.map((x) => <option key={x.name} value={x.name}>{x.name} · {x.title}</option>)}
            </select>
          </label>
          <textarea className="mono" rows={18} value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
        </details>
        <div className="actions">
          <button className="primary" disabled={busy || !execution.ready || !proposal.trim() || !runId || !yaml} onClick={submit}>{busy ? t('正在启动…') : t('开始完善')}</button>
        </div>
      </div>
    </section>
  );
}
