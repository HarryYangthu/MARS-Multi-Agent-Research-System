import type { RunDetail } from '../api';
import { useT } from '../prefs';

/** The Model RSI loop for each kind of run; the approval step is where the run pauses for the researcher. */
const FLOWS: Record<'rsi' | 'refine', string[]> = {
  rsi: ['分析基线、历史实验和知识库，提出方向', '检索文献并生成核验的证据卡', '独立提案、质询、答辩和双评委排序',
    '停下来等你确认进入进化的方案', '实现、检查、训练和诊断，逐轮进化', '筛选通过后做完整预算验收'],
  refine: ['把你的想法结构化成可执行方案', '预检参数量与约束', '停下来等你确认',
    '实现、检查、训练和诊断，逐轮进化', '筛选通过后做完整预算验收'],
};
const APPROVAL: Record<'rsi' | 'refine', number> = { rsi: 3, refine: 2 };

/** Which step a run is on, from the core's phase and the Step1 artifacts written so far. */
export function currentStep(run: RunDetail): { kind: 'rsi' | 'refine'; index: number; finished: boolean } {
  const kind = run.kind === 'refine' ? 'refine' : 'rsi';
  if (run.phase === 'finished') return { kind, index: FLOWS[kind].length, finished: true };
  if (run.phase === 'awaiting_approval') return { kind, index: APPROVAL[kind], finished: false };
  if (run.phase === 'step2') {
    const verifying = run.routes.some((r) => r.iterations.some((it) => it.screen_passed && it.verify_passed == null));
    return { kind, index: APPROVAL[kind] + (verifying ? 2 : 1), finished: false };
  }
  const done = new Set((run.step1[run.step1.length - 1]?.stages ?? []).filter((s) => s.done).map((s) => s.key));
  if (kind === 'refine') return { kind, index: done.has('proposals') || done.has('debate') ? 1 : 0, finished: false };
  const index = done.has('proposals') || done.has('critiques') || done.has('debate') ? 2 : done.has('sources') || done.has('cards') ? 1
    : done.has('analysis') ? 1 : 0;
  return { kind, index, finished: false };
}

/**
 * "Model RSI": the steps a run goes through. Without `current` it explains what will happen (new-run page); with
 * it, the step the run is on is highlighted and earlier ones are ticked (workbench).
 */
export function RsiFlow({ kind, current, active = false, humanApproval = true }: {
  kind: 'rsi' | 'refine'; current?: number; active?: boolean; humanApproval?: boolean;
}) {
  const t = useT();
  return <ol className="rsi-flow">
    {FLOWS[kind].map((text, i) => {
      const state = current === undefined ? '' : i < current ? 'done' : i === current ? (active ? 'now' : 'paused') : '';
      const approval = i === APPROVAL[kind];
      return <li key={text} className={state} aria-current={state === 'now' || state === 'paused' ? 'step' : undefined}>
        <span className="rsi-mark" aria-hidden="true">{state === 'done' ? '✓' : i + 1}</span>
        <span>{t(approval && !humanApproval ? '按评审排序自动选择方案' : text)}
          {approval && humanApproval && current === undefined && <span className="small muted"> · {t('在这里暂停')}</span>}</span>
      </li>;
    })}
  </ol>;
}

/** Where the run is in the Model RSI loop; collapsed once it has finished. */
export function RunFlow({ run }: { run: RunDetail }) {
  const t = useT();
  const { kind, index, finished } = currentStep(run);
  const humanApproval = (run.contract as { human_approval?: boolean }).human_approval !== false;
  return <details className="card run-flow" open={!finished}>
    <summary>Model RSI · {finished ? t('已完成') : run.state === 'running' ? t('正在进行第 {n} 步', { n: index + 1 })
      : run.state === 'waiting_approval' ? t('停在第 {n} 步，等你确认', { n: index + 1 }) : t('停在第 {n} 步', { n: index + 1 })}</summary>
    <RsiFlow kind={kind} current={index} active={run.state === 'running'} humanApproval={humanApproval} />
  </details>;
}

