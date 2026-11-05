import type { Followup } from '../api';
import { num } from './ui';
import { useT } from '../prefs';

const metricNames = (experiment: Followup) => [...new Set(experiment.rows.flatMap((row) => Object.keys(row.metrics ?? {})))];

export function FollowupExperiments({ experiments, onSelect }: {
  experiments: Followup[]; onSelect: (route: string, iteration: number) => void;
}) {
  const t = useT();
  return <div className="stack">{experiments.map((experiment, i) => {
    const search = experiment.kind === 'search';
    return <details className="card" key={`${experiment.kind}:${experiment.route}:${experiment.iteration}`} open={i === 0}>
      <summary>{experiment.route} · {t('第 {k} 轮', { k: experiment.iteration })} · {t(search ? '超参对比' : '多种子复核')}
        {experiment.dataset ? ` · ${experiment.dataset}` : ''}{experiment.epochs ? ` · ${experiment.epochs} epoch` : ''}</summary>
      <div className="actions" style={{ marginBlock: 12 }}>
        <button type="button" onClick={() => onSelect(experiment.route, experiment.iteration)}>{t('查看对应轮次')}</button>
      </div>
      <div style={{ overflowX: 'auto' }}><table>
        <thead><tr>{[search ? '数据集' : '对象', search ? '参数' : '种子'].map((label) => <th key={label}>{t(label)}</th>)}
          {metricNames(experiment).map((m) => <th key={m}>{m}</th>)}
          {search && metricNames(experiment).map((m) => <th key={`b-${m}`}>{t('基线')} {m}</th>)}</tr></thead>
        <tbody>{experiment.rows.map((row, j) => <tr key={j}>
          {search ? <><td>{row.dataset}</td><td className="mono small">{Object.entries(row.params ?? {}).map(([k, v]) => `${k}=${String(v)}`).join(' ')}</td></>
            : <><td>{t(row.role === 'baseline' ? '基线' : '候选')}</td><td>{typeof row.seed === 'string' ? t(row.seed) : row.seed}</td></>}
          {metricNames(experiment).map((m) => <td key={m}>{num(row.metrics?.[m] ?? null, 4)}</td>)}
          {search && metricNames(experiment).map((m) => <td key={`b-${m}`}>{num(row.baseline_metrics?.[m] ?? null, 4)}</td>)}
        </tr>)}</tbody>
      </table></div>
      <details className="small" style={{ marginTop: 12, overflowWrap: 'anywhere' }}><summary>{t('来源')}</summary>
        <p className="mono">{experiment.source.file}</p>
        <p className="mono">SHA256 {experiment.source.sha256}</p>
        <p className="mono">{t('代码版本')} {experiment.commit}</p>
      </details>
    </details>;
  })}</div>;
}
