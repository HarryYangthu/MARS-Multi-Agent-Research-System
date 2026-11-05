import type { MetricsDecl, StageEntry } from '../api';
import { metricLabel } from '../metrics';
import { useT } from '../prefs';
import { num, signed } from './ui';

/** Candidate, baseline and gap for every declared metric of one dataset at one stage. */
export function MetricTable({ entry, metrics }: { entry: StageEntry | undefined; metrics: MetricsDecl }) {
  const t = useT();
  if (!entry) return null;
  if (entry.ok === false) return <p className="tone-bad-text small">{entry.error ?? t('评测失败')}</p>;
  const names = Object.keys(metrics.fields).filter((n) => entry.metrics?.[n] !== undefined);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>{t('指标')}</th><th>{t('候选')}</th><th>{t('基线')}</th><th>{t('差于基线')}</th><th>{t('判定')}</th></tr></thead>
        <tbody>
          {names.map((n) => {
            const gate = entry.gates?.[n];
            const primary = n === entry.primary;
            const verdict = gate !== undefined ? (gate ? t('通过') : t('未通过'))
              : primary && entry.passed !== undefined && entry.passed !== null ? (entry.passed ? t('噪声门槛内') : t('超出噪声门槛')) : '';
            return (
              <tr key={n}>
                <td>{metricLabel(metrics, n)}{primary && <span className="muted small"> · {t('主指标')}</span>}</td>
                <td className="num">{num(entry.metrics?.[n], 4)}</td>
                <td className="num">{num(entry.baseline_metrics?.[n], 4)}</td>
                <td className="num">{signed(entry.worse_by?.[n], 4)}</td>
                <td className={gate === false || (primary && entry.passed === false) ? 'tone-bad-text' : ''}>{verdict}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {entry.margin !== undefined && entry.margin !== null && <p className="muted small">{t('噪声门槛 {m}', { m: num(entry.margin, 4) })}</p>}
    </div>
  );
}
