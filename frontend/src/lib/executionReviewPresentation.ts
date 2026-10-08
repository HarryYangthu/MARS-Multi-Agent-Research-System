export type ExperimentConfiguration = {
  config: Record<string, unknown>; effective?: Record<string, unknown>;
};

// These fields come from the same host preparation used to launch the job.
// In a steps run, legacy epoch metadata is not the execution budget.
export function executionExperimentFields(item: ExperimentConfiguration): [string, unknown][] {
  const actual = item.effective || item.config;
  return [
    ["计划设备", item.config.device],
    ["线程数", actual.threads ?? item.config.threads],
    ["实际训练配置", actual.config_path ?? item.config.config_path],
    ["实际数据路径", actual.data_path],
    ["随机种子", actual.seed ?? item.config.seed],
    [actual.budget_unit === "steps" || item.config.budget_steps !== undefined ? "参数更新次数" : "完整训练轮次", actual.max_iters ?? item.config.budget_steps],
    ["配置允许的完整训练轮次", actual.configured_total_epochs],
    ["学习率调度单位", actual.scheduler_unit === "steps" ? "参数更新次数" : actual.scheduler_unit === "epochs" ? "完整训练轮次" : actual.scheduler_unit === "disabled" ? "未启用调度" : undefined],
    ["预算说明", actual.budget_warning],
    ["结果存放位置", actual.output_parent],
  ].filter((row): row is [string, unknown] => row[1] !== undefined && row[1] !== null && row[1] !== "");
}
