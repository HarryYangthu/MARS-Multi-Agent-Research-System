import { useEffect, useState } from 'react';
import { api, type ExecutionEnvironment, type ExecutionOption } from '../api';
import { useT } from '../prefs';

export function useExecutionEnvironment(initial: ExecutionEnvironment = 'local') {
  const [environment, setEnvironment] = useState<ExecutionEnvironment>(initial);
  const [options, setOptions] = useState<ExecutionOption[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    api.executionEnvironments().then(
      (result) => { if (alive) setOptions(result.options); },
      (reason: Error) => { if (alive) setError(reason.message); },
    );
    return () => { alive = false; };
  }, []);
  const selected = options.find((option) => option.id === environment);
  return { environment, setEnvironment, options, error, selected, ready: !!selected?.configured };
}

export function ExecutionEnvironmentPicker({ selection, disabled = false }: {
  selection: ReturnType<typeof useExecutionEnvironment>;
  disabled?: boolean;
}) {
  const t = useT();
  return <label>
    {t('训练执行环境')}
    <select value={selection.environment} disabled={disabled || !selection.options.length}
      onChange={(event) => selection.setEnvironment(event.target.value as ExecutionEnvironment)}>
      {!selection.options.length && <option value={selection.environment}>{t('正在读取…')}</option>}
      {selection.options.map((option) => <option key={option.id} value={option.id} disabled={!option.configured}>
        {t(option.label)}{option.device ? ` · ${option.device}` : ''}{!option.configured ? ` · ${t('尚未配置')}` : ''}
      </option>)}
    </select>
    {selection.error && <span className="small tone-bad-text" role="alert">{selection.error}</span>}
  </label>;
}
