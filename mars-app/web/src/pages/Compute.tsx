import { useCallback, useEffect, useState } from 'react';
import { api, type CoreJob, type Jobs } from '../api';
import { ErrorNote, Hint } from '../components/ui';
import { useT } from '../prefs';

const KIND: Record<string, string> = { train: '训练', codingagent: '编码 Agent', hpo: '超参寻优', verify: '仿真验证' };
const STAGE: Record<string, string> = { prequick: '起步检查', quick: '快速训练', screen: '筛选', verify: '完整验收', implement: '实现', reference: '参考评测' };

function since(started: CoreJob['started']): string {
  const at = typeof started === 'number' ? started * 1000 : started ? Date.parse(started) : NaN;
  if (!Number.isFinite(at)) return '—';
  const minutes = Math.max(0, Math.round((Date.now() - at) / 60000));
  return minutes >= 60 ? `${(minutes / 60).toFixed(1)} h` : `${minutes} min`;
}

/** What is computing now: jobs the core registered (training, coding) and this app's own searches and verifications. */
export function Compute() {
  const t = useT();
  const [jobs, setJobs] = useState<Jobs | null>(null);
  const [error, setError] = useState('');
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [filter, setFilter] = useState('all');

  const load = useCallback((remote = false) => {
    if (remote) setRemoteBusy(true);
    return api.jobs(remote).then((j) => (setJobs(j), setError('')), (e: Error) => setError(e.message)).finally(() => setRemoteBusy(false));
  }, []);
  useEffect(() => {
    load();
    const timer = window.setInterval(() => load(), 10000); // local state only; remote progress is read on request
    return () => window.clearInterval(timer);
  }, [load]);

  const progress = (job: CoreJob) => {
    const p = job.progress;
    if (!p) return job.target?.startsWith('local') ? '—' : t('远程训练，点上方按钮读取');
    const steps = typeof p.step === 'number' ? (p.total ? `${p.step}/${p.total}` : String(p.step)) : null;
    if (steps && typeof p.epoch === 'number') return t('第 {e} epoch · {s} 步', { e: p.epoch, s: steps });
    if (steps) return t('{s} 步', { s: steps });
    return typeof p.epoch === 'number' ? t('第 {e} epoch', { e: p.epoch }) : '—';
  };

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>{t('算力')}<Hint text="内核登记的训练与编码作业，以及本应用发起的超参寻优和仿真验证。每 10 秒刷新。" /></h1>
        </div>
        <button disabled={remoteBusy} onClick={() => load(true)} title={t('通过 ssh 读一次远程训练进度，每次约 1 秒')}>
          {remoteBusy ? t('正在读取…') : t('读取远程训练进度')}
        </button>
      </div>
      {error && <ErrorNote error={error} />}
      {jobs?.core_error && <ErrorNote error={jobs.core_error} />}
      {jobs && (
        <>
          <div className="stat-grid">
            <div className="card stat"><span className="muted small">{t('训练与编码作业')}</span><strong>{jobs.core.length}</strong><span className="muted small">{[...new Set(jobs.core.map((j) => j.target).filter(Boolean))].join(' · ') || t('暂无活动执行位置')}</span></div>
            <div className="card stat"><span className="muted small">{t('本应用作业')}</span><strong>{jobs.app.length}</strong>
              <span className="muted small">{t('调参与验证共用 {n} 个 GPU 名额', { n: jobs.max_parallel })}</span></div>
            <div className="card stat"><span className="muted small">{t('已记录的模型用量')}</span><strong>{jobs.usage?.known_calls ? (jobs.usage.input_tokens + jobs.usage.output_tokens).toLocaleString() : '—'}</strong><span className="muted small">{t('tokens · 不含编码 Agent')}</span></div>
          </div>
          <div className="card">
            <div className="section-head"><h2>{t('正在做的事')}</h2><div className="pills">{[['all', '全部'], ['train', '训练'], ['codingagent', '模型与 Agent']].map(([id, name]) => <button type="button" key={id} className={filter === id ? 'on' : ''} aria-pressed={filter === id} onClick={() => setFilter(id)}>{t(name)}</button>)}</div></div>
            {jobs.core.length === 0 && jobs.app.length === 0 && (
              <p className="muted">{t('暂无运行中的作业')}</p>
            )}
            {(jobs.core.length > 0 || jobs.app.length > 0) && (
              <div className="table-wrap">
                <table className="job-table">
                  <thead><tr><th>{t('类型')}</th><th>{t('内容')}</th><th>{t('执行位置')}</th><th>{t('进度')}</th><th>{t('已运行')}</th><th /></tr></thead>
                  <tbody>
                    {jobs.core.filter((job) => filter === 'all' || job.kind === filter).map((job, i) => (
                      <tr key={`c${i}`}>
                        <td><span className={`badge tone-${job.kind === 'train' ? 'info' : 'wait'}`}>{t(KIND[job.kind] ?? job.kind)}</span></td>
                        <td>
                          <span className="mono">{job.route === 'baseline' ? t('基线') : job.route}</span>
                          {job.iteration !== null && ` · ${t('第 {k} 轮', { k: job.iteration })}`}
                          {job.stage && ` · ${t(STAGE[job.stage] ?? job.stage)}`}
                          {job.dataset && ` · ${job.dataset}`}
                        </td>
                        <td className="mono small">{job.target ?? '—'}</td>
                        <td>{job.kind === 'codingagent' ? '—' : progress(job)}</td>
                        <td className="num">{since(job.started)}</td>
                        <td><a href={`#/runs/${encodeURIComponent(job.run_id)}`}>{job.run_id}</a></td>
                      </tr>
                    ))}
                    {jobs.app.filter(() => filter !== 'codingagent').map((job) => (
                      <tr key={`a${job.id}`}>
                        <td><span className="badge tone-muted">{t(KIND[job.kind] ?? job.kind)}</span></td>
                        <td>{job.title || job.id}</td>
                        <td className="muted">—</td>
                        <td>{job.counts ? t('完成 {c} · 失败 {f} · 进行中 {r}', { c: job.counts.complete ?? 0, f: job.counts.fail ?? 0, r: job.counts.running ?? 0 }) : job.state}</td>
                        <td className="num">—</td>
                        <td><a href={`#/${job.kind}/${encodeURIComponent(job.id)}`}>{t('查看')}</a></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <span className="small muted">{t('模型用量')}<Hint text={`${t('模型用量统计只覆盖内核自己发起的调用，不含编码 Agent。')} ${t('已记录 {n} 次调用，另有 {m} 次历史调用没有用量字段。', { n: jobs.usage?.known_calls ?? 0, m: jobs.usage?.unknown_calls ?? 0 })}`} /></span>
          <div className="card"><h2>{t('最近完成')}</h2>{!jobs.recent?.length && <p className="muted">{t('还没有完成的训练记录。')}</p>}{jobs.recent?.map((row, i) => <a className="run-row" key={`${row.run_id}:${row.route}:${row.iteration}:${row.stage}:${i}`} href={`#/runs/${encodeURIComponent(row.run_id)}?route=${encodeURIComponent(row.route)}&iteration=${row.iteration}`}>
            <span className={`badge tone-${row.passed ? 'good' : 'bad'}`}>{t(row.passed ? '✓ 通过' : '✕ 未通过')}</span><span className="mono">{row.route} · {t('第 {k} 轮', { k: row.iteration })}</span><span className="small">{t(STAGE[row.stage] ?? row.stage)}</span><span className="muted small ellipsis">{row.run_id}</span>
          </a>)}</div>
        </>
      )}
    </section>
  );
}
