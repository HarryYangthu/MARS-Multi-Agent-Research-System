import { useEffect, useState } from 'react';
import { api, type EpochInfo } from '../api';
import { useT } from '../prefs';
import { Hint, datasetOrder } from './ui';

/**
 * Evaluation budget in the project's steps, with the epoch equivalent per dataset when the project declares a
 * steps_per_epoch diagnostic (asked once per target). 0 = the project's full budget.
 */
export function StepsInput({ steps, onChange, candidateDir, overrides, hint }: {
  steps: number; onChange: (steps: number) => void; candidateDir?: string | null; overrides?: Record<string, unknown>; hint?: string;
}) {
  const t = useT();
  const [unit, setUnit] = useState<'steps' | 'epochs'>('steps');
  const [info, setInfo] = useState<EpochInfo | null>(null);
  const [error, setError] = useState('');
  const key = JSON.stringify([candidateDir ?? null, overrides ?? {}]);
  useEffect(() => {
    let alive = true;
    setInfo(null); setError('');
    api.stepsPerEpoch(candidateDir ?? null, overrides ?? null).then((found) => alive && setInfo(found), (e: Error) => alive && setError(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const datasets = Object.keys(info ?? {}).sort(datasetOrder);
  const per = datasets.map((d) => info![d].steps_per_epoch);
  const uniform = per.length > 0 && per.every((n) => n === per[0]) ? per[0] : null;
  const epochs = (d: string) => (steps === 0 ? info![d].epochs : steps / info![d].steps_per_epoch);
  const show = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(n < 1 ? 2 : 1));

  return <label>
    <span>{t('训练步数')}<Hint text={t('步数由项目的评测命令解释；0 = 项目的完整预算。') + (hint ? `\n${t(hint)}` : '')} /></span>
    <span className="steps-input">
      <input type="number" min={0} step={unit === 'epochs' ? 'any' : 1}
        value={unit === 'steps' || uniform === null ? steps : show(steps / uniform)}
        onChange={(e) => {
          const value = Math.max(0, Number(e.target.value));
          onChange(unit === 'epochs' && uniform ? Math.round(value * uniform) : Math.round(value));
        }} />
      <span className="seg" role="group" aria-label={t('单位')}>
        <button type="button" className={unit === 'steps' ? 'on' : ''} onClick={() => setUnit('steps')}>{t('步')}</button>
        <button type="button" className={unit === 'epochs' ? 'on' : ''} disabled={uniform === null} onClick={() => setUnit('epochs')}
          title={uniform === null ? t('各数据集每 epoch 步数不同，请按步输入') : ''}>epoch</button>
      </span>
    </span>
    <span className="muted small">
      {info && datasets.length > 0 && (uniform !== null
        ? t('≈ {e} epoch（每 epoch {n} 步）', { e: show(epochs(datasets[0])), n: uniform })
        : datasets.map((d) => t('{d} ≈ {e} epoch', { d, e: show(epochs(d)) })).join(' · '))}
      {!info && !error && t('正在计算 epoch…')}
      {error && <span className="tone-bad-text">{t('无法折算 epoch：{e}', { e: error })}</span>}
    </span>
  </label>;
}
