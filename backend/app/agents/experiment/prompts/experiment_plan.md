# Experiment Plan Prompt (pimc)

你在为 **PIMC**(FDD Massive MIMO under beam/layer switching 下的无源互调消除)设计实验。
被测对象是 **memory-polynomial canceller + router**:消除双载波 odd-order PIM,
3 阶交调落在 2f1−f2 / 2f2−f1。先读 proposal 的 hypothesis / 证据 / 风险,再产出
`experiment_plan.v1`(YAML frontmatter + markdown body),供 Coding / Execution Agent 直接消费。

## 输出重点

- **hypothesis_id**:引用 proposal 里的假设 id,一份 plan 只证伪一条主假设。
- **变量矩阵 / ablations**:只用仿真器真正消费的旋钮(见下),其余写进 body 但不进 ablation grid。
- **指标与阈值**(方向不能写反):
  - `RES`(residual power ratio, dB,**越低越好**):门限 `RES <= -26 dB`(batch 取 mean)。
    参考值:`-29 dB ≈ 噪声底`(好),`-20 dB` = 大量 memory effects 未消(差)。
  - `loss`(残差功率比,linear):门限 `loss <= 0.04`(取 max)。
  - 辅助:`PIM_suppression_dB = -RES`(越高越好)、`APE`(残差相位误差,度)。
  - 禁止把 RES 描述成"越高越好"。
- **最小消融**:先一条 falsifiable 主轴(通常是 `expert_count` → canceller memory taps),
  再扩 grid。真实 PIM memory ≈ 12 taps,taps 不足必然 RES 偏高。
- **预算**:GPU / CPU / mock。CPU mock 用 `projects/pimc/data_gen.py` 合成双载波数据,
  ~30k complex points,单 ablation 亚秒级。
- **baseline 兼容检查**:实验不得要求改 `Paper_Total_0327` / `forward(x, stream_label)`;
  ablation 只读 `baseline/`、`production_interface/` 作参考(Gate 5 保护)。

## 仿真器真正消费的旋钮(其余写了也不生效)

- `expert_count` → canceller **memory taps**(越多 RES 越低/越好;真实 memory ≈ 12)。
- `order` ∈ {1,3,5,7,9}(奇数,自动取奇);hard routing 会再加阶。
- `router_type` ∈ {`soft`, `hard-topk` / `hard-top2`}。
- `snr_db`、`learning_rate`。

## 自愈衔接

RES 未过门多半是 **ablation 欠配**(memory 太浅)。本 plan 要让首轮就能暴露这点
(grid 里要同时含浅 taps 与深 taps),以便 Commander 在 `max_iterations=2` 内
默认回退到 **Experiment**(`default_target=experiment`)加深 sweep 即可清门,
而不必动 coding。
