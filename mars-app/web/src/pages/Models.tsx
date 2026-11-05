import { useEffect, useRef, useState } from 'react';
import { api, type ModelCheck, type ModelRole, type ModelsView, type ProbeResult } from '../api';
import { ErrorNote, Hint } from '../components/ui';
import { useT } from '../prefs';
import { useConfirm } from '../components/Confirm';

const ROLE_INFO: Record<string, string> = {
  strong: '分析、提方案、辩论、诊断决策：低频高价值（内网 GLM-5.x）',
  fast: '检索规划、阅读摘要、证据卡：SH model1 / model2',
  coder: 'codingagent 编码用：必须支持原生工具调用',
};
const THINKING_LABEL: Record<string, string> = {
  none: '不传开关', zhipu_enabled: 'thinking.type=enabled', zhipu_disabled: 'thinking.type=disabled', template_enable_false: 'enable_thinking=false', template_enable_true: 'enable_thinking=true',
};

type Tri = '' | 'true' | 'false';
type RoleForm = {
  provider: string; model: string; max_tokens: string; timeout: string; idle_timeout: string; concurrency: string;
  stream: Tri; json_mode: Tri; thinking: Tri; single_user: Tri; reasoning_effort: string; extra_body: string;
};
type ProviderForm = { name: string; api_base: string; api_key_env: string };

const tri = (v: unknown): Tri => (v === true ? 'true' : v === false ? 'false' : '');
const fromTri = (v: Tri): boolean | null => (v === 'true' ? true : v === 'false' ? false : null);
const str = (v: unknown) => (v === undefined || v === null ? '' : String(v));

function toRoleForm(role: ModelRole): RoleForm {
  return {
    provider: str(role.provider), model: str(role.model), max_tokens: str(role.max_tokens), timeout: str(role.timeout),
    idle_timeout: str(role.idle_timeout), concurrency: str(role.concurrency), stream: tri(role.stream),
    json_mode: tri(role.json_mode), thinking: tri(role.thinking), single_user: tri(role.single_user), reasoning_effort: str(role.reasoning_effort),
    extra_body: role.extra_body ? JSON.stringify(role.extra_body, null, 2) : '',
  };
}

/** Form → role fields for the API; null means "not set" (the server removes the field from models.yaml). */
function toRole(form: RoleForm, name: string, t: ReturnType<typeof useT>): Record<string, unknown> {
  const int = (label: string, v: string) => {
    if (!v.trim()) return null;
    const n = Number(v);
    if (!Number.isInteger(n) || n <= 0) throw new Error(t('{name}：{label} 必须是正整数', { name, label }));
    return n;
  };
  let extra: Record<string, unknown> | null = null;
  if (form.extra_body.trim()) {
    try {
      extra = JSON.parse(form.extra_body) as Record<string, unknown>;
    } catch {
      throw new Error(t('{name}：extra_body 不是合法的 JSON', { name }));
    }
    if (typeof extra !== 'object' || extra === null || Array.isArray(extra)) throw new Error(t('{name}：extra_body 必须是 JSON 对象', { name }));
  }
  return {
    provider: form.provider, model: form.model.trim(), max_tokens: int('max_tokens', form.max_tokens),
    timeout: int('timeout', form.timeout), idle_timeout: int('idle_timeout', form.idle_timeout),
    concurrency: int('concurrency', form.concurrency), stream: fromTri(form.stream), json_mode: fromTri(form.json_mode), single_user: form.single_user === 'true' ? true : null,
    thinking: fromTri(form.thinking), reasoning_effort: form.reasoning_effort.trim() || null, extra_body: extra,
  };
}

function Probe({ label, result }: { label: string; result?: ProbeResult }) {
  const t = useT();
  if (!result) return null;
  const skipped = result.skipped || result.error?.startsWith('未执行：');
  const detail = result.error ?? [
    result.transport ? t(result.transport === 'stream' ? '流式' : '非流式') : '',
    result.seconds !== undefined ? `${result.seconds}s` : '',
    result.status ? `HTTP ${result.status}` : '',
    result.reasoning_chars ? t('思考 {n} 字', { n: result.reasoning_chars }) : '',
    result.think_tag ? t('正文含 <think>') : '',
    result.sse_body_for_non_stream ? t('非流式返回了 SSE') : '',
    result.native_tool_call === false ? t('没有原生工具调用') : '',
    result.arguments_valid === false ? t('工具参数未通过校验') : '',
    result.reply ? t('回复：{r}', { r: result.reply }) : '',
  ].filter(Boolean).join(' · ');
  return (
    <tr>
      <td>{t(label)}</td>
      <td>{skipped ? <span className="muted">{t('未检测')}</span> : result.ok ? <span className="tone-good-text">{t('可用')}</span> : <span className="tone-bad-text">{t('不可用')}</span>}</td>
      <td className="small">{result.transport && result.error ? `${t(result.transport === 'stream' ? '流式' : '非流式')} · ` : ''}{detail}</td>
    </tr>
  );
}

function CheckReport({ check, form, onApply }: { check: ModelCheck; form: RoleForm; onApply?: (patch: Partial<RoleForm>) => void }) {
  const t = useT();
  if (check.status === 'running') return <p className="muted small">{t('正在检查模型连接与调用能力…')}</p>;
  if (check.status === 'failed' || !check.report) return <ErrorNote error={t('检查失败：{e}', { e: check.error ?? t('没有产出报告') })} />;
  const r = check.report;
  if (r.error) return <ErrorNote error={t('检查失败：{e}', { e: r.error })} />;
  const s = r.suggested ?? {};
  const patch: Partial<RoleForm> = {};
  if (typeof s.stream === 'boolean') patch.stream = tri(s.stream);
  if (typeof s.json_mode === 'boolean') patch.json_mode = tri(s.json_mode);
  if (s.to_disable_thinking) {
    try {
      const extra = JSON.parse(form.extra_body || '{}');
      if (extra && typeof extra === 'object' && !Array.isArray(extra)) {
        delete extra.thinking;
        delete extra.reasoning_effort;
        delete extra.enable_thinking;
        const template = { ...extra.chat_template_kwargs };
        delete template.enable_thinking;
        Object.assign(template, s.to_disable_thinking.chat_template_kwargs ?? {});
        if (Object.keys(template).length) extra.chat_template_kwargs = template;
        else delete extra.chat_template_kwargs;
        patch.thinking = 'thinking' in s.to_disable_thinking ? 'false' : '';
        patch.reasoning_effort = '';
        patch.extra_body = Object.keys(extra).length ? JSON.stringify(extra, null, 2) : '';
      }
    } catch { /* Keep invalid edits intact; transport suggestions can still be applied. */ }
  }
  return (
    <div className="check-report">
      <p className="small">
        {t('{ts} 完成', { ts: check.finished })} · {r.model} @ {r.api_base} ·{' '}
        {check.passed ? <span className="tone-good-text">{t(r.report_version === 2 ? '角色调用验证通过' : '至少一种调用方式可用')}</span> : <span className="tone-bad-text">{t('未通过')}</span>}
      </p>
      <table>
        <tbody>
          <Probe label="直接 HTTP" result={r.raw} />
          <Probe label="非流式" result={r.nonstream} />
          <Probe label="流式" result={r.stream} />
          <Probe label="JSON 模式" result={r.json} />
          <Probe label="内核结构校验" result={r.structured} />
          <Probe label="工具调用" result={r.tools} />
          <Probe label="工具结果回传" result={r.tools?.round_trip} />
          {Object.entries(r.thinking ?? {}).map(([k, v]) => <Probe key={k} label={`${t('思考')}：${THINKING_LABEL[k] ? t(THINKING_LABEL[k]) : k}`} result={v} />)}
        </tbody>
      </table>
      {r.thinking_switches && <p className="muted small">{t(Object.values(r.thinking_switches).some((v) => v.status === 'observed_disabled')
        ? '本次同题对照观察到思考内容消失；不代表所有输入都能关闭内部推理。'
        : '本次未能确认思考开关生效；请求成功不等于开关有效。')}</p>}
      {(s.note_raw || s.coder) && <div className="alert alert-warn small">{[s.note_raw, s.coder].filter(Boolean).join('；')}</div>}
      {Object.keys(patch).length > 0 && onApply && (
        <div className="approval-foot">
          <span className="small muted">
            {t('建议：')}{patch.stream ? `stream: ${patch.stream}；` : ''}{patch.json_mode ? `json_mode: ${patch.json_mode}；` : ''}
            {patch.thinking ? 'thinking: false；' : ''}{patch.extra_body ? `extra_body: ${JSON.stringify(s.to_disable_thinking)}${t('（关闭思考）')}` : ''}
          </span>
          <button onClick={() => onApply(patch)}>{t('采用建议（再保存才生效）')}</button>
        </div>
      )}
    </div>
  );
}

export function Models() {
  const confirm = useConfirm();
  const t = useT();
  const [view, setView] = useState<ModelsView | null>(null);
  const [providers, setProviders] = useState<ProviderForm[]>([]);
  const [roles, setRoles] = useState<Record<string, RoleForm>>({});
  const [fallbacks, setFallbacks] = useState<Record<string, string>>({});
  const [retries, setRetries] = useState('');
  const [keyInput, setKeyInput] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<Record<string, ModelCheck>>({});
  const [skipThinking, setSkipThinking] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const polls = useRef<Record<string, number>>({});

  const load = (v: ModelsView) => {
    const usedKeys = new Set<string>();
    let changed = false;
    const connections = Object.entries(v.roles).map(([name, role]) => {
      const original = v.providers[role.provider ?? ''];
      const key = original?.api_key_env ?? '';
      const independentKey = usedKeys.has(key) ? `MARS_${name.toUpperCase()}_API_KEY` : key;
      usedKeys.add(independentKey);
      changed ||= role.provider !== name || independentKey !== key;
      return { name, api_base: str(original?.api_base), api_key_env: independentKey };
    });
    setView(v);
    setProviders(connections);
    setRoles(Object.fromEntries(Object.entries(v.roles).map(([name, r]) => [name, toRoleForm({ ...r, provider: name })])));
    setFallbacks(v.fallbacks);
    setRetries(str(v.retries));
    setChecks(v.checks);
    setDirty(changed);
  };

  useEffect(() => {
    api.models().then(load, (e: Error) => setError(e.message));
    const timers = polls.current;
    return () => Object.values(timers).forEach((timer) => window.clearInterval(timer));
  }, []);

  if (!view) return error ? <ErrorNote error={error} /> : <p className="muted">{t('正在读取模型配置…')}</p>;
  const readOnly = !view.editable;
  const touch = () => (setDirty(true), setNotice(''));
  const updateRole = (name: string, change: Partial<RoleForm>) => (setRoles((rs) => ({ ...rs, [name]: { ...rs[name], ...change } })), touch());
  const updateProvider = (index: number, change: Partial<ProviderForm>) => (setProviders((ps) => ps.map((p, i) => (i === index ? { ...p, ...change } : p))), touch());

  const save = async () => {
    setError('');
    try {
      const body = {
        providers: Object.fromEntries(providers.map((p) => [p.name.trim(), { api_base: p.api_base.trim(), api_key_env: p.api_key_env.trim() }])),
        roles: Object.fromEntries(Object.entries(roles).map(([name, form]) => [name, toRole(form, name, t)])),
        fallbacks: Object.fromEntries(Object.entries(fallbacks).filter(([, v]) => v)),
        retries: retries.trim() ? Number(retries) : null,
      };
      if (!(await confirm(t('保存到 {p}？', { p: view.path }) + '\n' + t('上一版本会备份在同目录。只对之后新启动或续跑的运行生效，正在运行的不受影响。')))) return;
      setBusy(true);
      let saved = await api.modelsSave(body);
      for (const provider of providers) {
        const value = keyInput[provider.api_key_env];
        if (value) saved = { ...saved, keys: (await api.modelsKey(provider.api_key_env, value)).keys };
      }
      setKeyInput({});
      load(saved);
      setNotice(t('已保存。新启动或续跑的运行会使用新配置。'));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const check = async (role: string) => {
    if (dirty) {
      setError(t('请先保存当前的地址、模型和 Key，再测试连接。'));
      return;
    }
    if (!(await confirm(t('测试 {role}：最多发 {n} 次短请求，消耗少量额度。', { role: role === 'coder' ? 'coding' : role, n: skipThinking[role] ? 7 : 12 })))) return;
    setError('');
    try {
      const { id } = await api.modelsCheck(role, Boolean(skipThinking[role]));
      const poll = async () => {
        const result = await api.modelsCheckStatus(id);
        setChecks((c) => ({ ...c, [role]: result }));
        if (result.status !== 'running') window.clearInterval(polls.current[role]);
      };
      await poll();
      polls.current[role] = window.setInterval(poll, 3000);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>{t('模型连接')}<Hint text="内核按角色调用模型。兼容参数以“测试连接”的实测结果为准，不要按型号猜。说明见 mars-deploy/docs/models.md。" /></h1>
          <p className="muted small mono">{view.path}{view.editable ? '' : t('（只读）')}</p>
        </div>
        {view.editable && (
          <div className="actions">
            {dirty && <span className="small tone-bad-text">{t('有未保存的修改')}</span>}
            <button className="primary" disabled={busy || !dirty} onClick={save}>{busy ? t('正在保存…') : t('保存配置')}</button>
          </div>
        )}
      </div>
      {readOnly && (
        <div className="alert alert-info">
          <strong>{t('只读：没有设置 MARS_MODELS_CONFIG')}</strong>
          <Hint text={t('内核正在用自带的 configs/models.yaml，应用不修改内核目录。要在这里编辑，请启动 mars-app 时用环境变量 MARS_MODELS_CONFIG 指向一个配置文件（内网部署包会自动指向 local/models.yaml）。测试连接仍可使用。')} />
        </div>
      )}
      {view.error && <ErrorNote error={t('读取配置失败：{e}', { e: view.error })} />}
      {error && <ErrorNote error={error} />}
      {notice && <div className="alert alert-info">{notice}</div>}

      {Object.entries(roles).map(([name, form]) => {
        const index = providers.findIndex((p) => p.name === name);
        const provider = providers[index];
        const key = provider?.api_key_env ?? '';
        const status = view.keys[key];
        const label = name === 'coder' ? 'coding' : name;
        return (
        <div key={name} className="card">
          <div className="run-card-head">
            <h2>{label}</h2>
            <span className="muted small">{ROLE_INFO[name] ? t(ROLE_INFO[name]) : ''}</span>
          </div>
          <div className="form role-form">
            <div className="form-row">
              <label>{t('服务地址')}
                <input className="mono" aria-label={`${label} 服务地址`} value={provider?.api_base ?? ''} disabled={readOnly}
                  onChange={(e) => updateProvider(index, { api_base: e.target.value })} />
              </label>
              <label>{t('模型 ID（区分大小写）')}
                <input className="mono" aria-label={`${label} 模型`} list={`models-${name}`} value={form.model} disabled={readOnly} onChange={(e) => updateRole(name, { model: e.target.value })} />
                <datalist id={`models-${name}`}>
                  {(view.providers[view.roles[name]?.provider ?? '']?.models ?? []).map((model) => <option key={model} value={model} />)}
                </datalist>
              </label>
              <label>API Key
                <div className="key-row">
                  <input type="password" aria-label={`${label} API Key`} autoComplete="off"
                    placeholder={status?.set ? t('已保存；填写新 Key 可替换') : t('填写此角色的 Key')}
                    value={keyInput[key] ?? ''} disabled={readOnly}
                    onChange={(e) => (setKeyInput((k) => ({ ...k, [key]: e.target.value })), touch())} />
                  <span className={`badge ${status?.set ? 'tone-good' : 'tone-bad'}`}>{t(status?.set ? '已设置' : '未设置')}</span>
                </div>
              </label>
            </div>
            <details>
              <summary>{t('高级参数')}</summary>
              <div className="form-row">
              <label>max_tokens
                <input type="number" value={form.max_tokens} disabled={readOnly} onChange={(e) => updateRole(name, { max_tokens: e.target.value })} />
                <span className="muted small">{t('思考与正文共用')}</span>
              </label>
              <label>{t('并发')}
                <input type="number" value={form.concurrency} disabled={readOnly} onChange={(e) => updateRole(name, { concurrency: e.target.value })} />
              </label>
            </div>
            <div className="form-row">
              <label>{t('流式')}
                <select value={form.stream} disabled={readOnly} onChange={(e) => updateRole(name, { stream: e.target.value as Tri })}>
                  <option value="">{t('流式（默认）')}</option>
                  <option value="false">{t('非流式（stream: false）')}</option>
                  <option value="true">{t('流式（stream: true）')}</option>
                </select>
              </label>
              <label>{t('timeout（秒）')}
                <input type="number" value={form.timeout} disabled={readOnly} onChange={(e) => updateRole(name, { timeout: e.target.value })} />
                <span className="muted small">{form.stream === 'false' ? t('非流式：排队加完整生成的总时长') : t('单次调用总时长上限')}</span>
              </label>
              <label>{t('idle_timeout（秒）')}
                <input type="number" value={form.idle_timeout} disabled={readOnly || form.stream === 'false'} onChange={(e) => updateRole(name, { idle_timeout: e.target.value })} />
                <span className="muted small">{form.stream === 'false' ? t('非流式时不生效') : t('两次输出之间允许的静默')}</span>
              </label>
              <label>{t('JSON 模式')}
                <select value={form.json_mode} disabled={readOnly} onChange={(e) => updateRole(name, { json_mode: e.target.value as Tri })}>
                  <option value="">{t('发送（默认）')}</option>
                  <option value="false">{t('不发送（json_mode: false）')}</option>
                </select>
              </label>
              <label>{t('消息格式')}
                <select value={form.single_user === 'true' ? 'true' : ''} disabled={readOnly} onChange={(e) => updateRole(name, { single_user: e.target.value as Tri })}>
                  <option value="">{t('标准多条消息（默认）')}</option>
                  <option value="true">{t('只发一条 user 消息（single_user: true）')}</option>
                </select>
                <span className="muted small">{t('部分内网网关只接受一条 user 消息时选这个')}</span>
              </label>
            </div>
            <div className="form-row">
              <label>{t('thinking（智谱云写法）')}
                <select value={form.thinking} disabled={readOnly} onChange={(e) => updateRole(name, { thinking: e.target.value as Tri })}>
                  <option value="">{t('不设置')}</option>
                  <option value="true">{t('开启')}</option>
                  <option value="false">{t('关闭')}</option>
                </select>
                <span className="muted small">{t('自部署 vLLM/SGLang 用下面的 extra_body')}</span>
              </label>
              <label>reasoning_effort
                <input value={form.reasoning_effort} disabled={readOnly} placeholder={t('不确定时留空')} onChange={(e) => updateRole(name, { reasoning_effort: e.target.value })} />
              </label>
              <label className="grow">{t('extra_body（JSON，随请求体发送）')}
                <textarea className="mono" rows={4} value={form.extra_body} disabled={readOnly}
                  placeholder={`${t('例如：')}{"chat_template_kwargs": {"enable_thinking": false}}`} onChange={(e) => updateRole(name, { extra_body: e.target.value })} />
              </label>
            </div>
            </details>
          </div>
          <div className="approval-foot">
            <label className="inline">
              <input type="checkbox" checked={Boolean(skipThinking[name])} onChange={(e) => setSkipThinking((s) => ({ ...s, [name]: e.target.checked }))} />
              {t('跳过思考开关探测（少 4 次请求）')}
            </label>
            <button disabled={!view.check_available || checks[name]?.status === 'running'} onClick={() => check(name)}
              title={view.check_available ? '' : t('找不到 {p}', { p: view.check_script })}>
              {checks[name]?.status === 'running' ? t('检查中…') : t('测试 {role} 连接', { role: label })}
            </button>
          </div>
          {!view.check_available && <p className="muted small">{t('测试连接需要 mars-deploy 的 {p}。', { p: view.check_script })}</p>}
          {checks[name] && <CheckReport check={checks[name]} form={form} onApply={view.editable ? (patch) => updateRole(name, patch) : undefined} />}
        </div>
      ); })}

      <div className="card">
        <h2>{t('降级与重试')}</h2>
        <div className="form-row">
          {Object.keys(roles).map((name) => (
            <label key={name}>{name} {t('失败后降级到')}
              <select value={fallbacks[name] ?? ''} disabled={readOnly} onChange={(e) => (setFallbacks((f) => ({ ...f, [name]: e.target.value })), touch())}>
                <option value="">{t('不降级')}</option>
                {Object.keys(roles).filter((r) => r !== name).map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
          ))}
          <label>{t('重试次数')}
            <input type="number" min={0} max={10} value={retries} disabled={readOnly} onChange={(e) => (setRetries(e.target.value), touch())} />
          </label>
        </div>
      </div>
    </section>
  );
}
