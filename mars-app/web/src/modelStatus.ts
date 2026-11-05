import { useEffect, useState } from 'react';
import { api, type ModelsView } from './api';

export type ModelState = 'loading' | 'error' | 'missing' | 'untested' | 'failed' | 'ok';

export interface Problem { text: string; params: Record<string, string> }

/**
 * Whether the models the core needs are usable, from the model settings: every required role has a provider and a
 * model, and that provider's key is set. "ok" additionally needs a passed connection test for each role; a key that
 * is set but never tested is "untested", which still allows a start.
 */
export function modelState(view: ModelsView): { state: ModelState; problems: Problem[] } {
  const problems: Problem[] = [];
  for (const role of view.roles_required) {
    const form = view.roles[role];
    const provider = form?.provider ? view.providers[form.provider] : undefined;
    if (!form?.provider || !form.model) problems.push({ text: '{role}：未配置服务或模型', params: { role } });
    else if (!provider) problems.push({ text: '{role}：服务 {p} 不存在', params: { role, p: form.provider } });
    else if (provider.api_key_env && !view.keys[provider.api_key_env]?.set) problems.push({ text: '{role}：{key} 未设置', params: { role, key: provider.api_key_env } });
  }
  if (problems.length) return { state: 'missing', problems };
  const checks = view.roles_required.map((role) => view.checks[role]);
  if (checks.some((c) => c && c.status !== 'running' && c.passed === false)) return { state: 'failed', problems };
  if (checks.every((c) => c?.passed)) return { state: 'ok', problems };
  return { state: 'untested', problems };
}

export function useModelStatus(): { state: ModelState; problems: Problem[]; error: string } {
  const [result, setResult] = useState<{ state: ModelState; problems: Problem[]; error: string }>({ state: 'loading', problems: [], error: '' });
  useEffect(() => {
    let alive = true;
    api.models().then((view) => alive && setResult({ ...modelState(view), error: '' }),
      (e: Error) => alive && setResult({ state: 'error', problems: [], error: e.message }));
    return () => { alive = false; };
  }, []);
  return result;
}

export const MODEL_LABEL: Record<ModelState, string> = {
  loading: '正在读取模型配置…',
  error: '读取模型配置失败',
  missing: '模型未配置好',
  untested: '已配置，尚未测试连接',
  failed: '连接测试未通过',
  ok: '连接测试通过',
};

/** Before a start that calls models: the reasons it would fail at once (no key, no model), empty when it can go. */
export async function missingModels(): Promise<Problem[]> {
  const found = modelState(await api.models());
  return found.state === 'missing' ? found.problems : [];
}
