# Coding Agent 工作原则 (moe-pimc)

Coding Agent 把 PIMC 实验方案转成代码规格和可审核补丁:对象是
**memory-polynomial canceller + MoE router**(消双载波 odd-order PIM,
3 阶交调落在 2f1−f2 / 2f2−f1)。

## 硬性原则

- **不改受保护 baseline**:`Paper_Total_0327`(`libs/Model.py`)的方法体与构造签名冻结;
  `baseline/**`、`production_interface/**` 对 MARS 只读。违反即被 Gate 5 在 tool dispatch
  路径直接拦截。
- **保持 `forward(x, stream_label)` 接口兼容**:第三个位置参数必须叫 `stream_label`;
  新功能只能加带默认值的 keyword-only 参数,禁止位置重排。
- **改动必须 ADDITIVE**:扩 memory taps / 换 router 一律走新 module 或子类
  (如 `DeepMemoryCanceller(Paper_Total_0327)`),baseline 原样保留作对照。
- 把仿真器真正消费的旋钮正确接上 canceller:`expert_count`(→ memory taps,真实 ≈ 12)、
  `order` ∈ {1,3,5,7,9}(奇)、`router_type` ∈ {soft, hard-topk/hard-top2}、
  `snr_db`、`learning_rate`。
- 每个 patch 说明文件、风险、测试、回滚路径;tensor op 前后必须带 shape 注释。
- 测试要锁住指标方向:`RES`(dB)**越低越好**,门限 `RES <= -26 dB`(mean)、
  `loss <= 0.04`(max),加深 taps 时 RES 应下降。**绝不**把 RES 当成越高越好。
- 新依赖必须有理由,能不用就不用。

## 与自愈环的关系

`max_iterations=2`,`allowed_targets=[experiment, coding]`,`default_target=experiment`。
RES 未过门多半是 experiment 侧 memory 欠配,会回到 Experiment 而非 Coding;
只有确属代码缺陷(旋钮没接上 / basis 构造错 / shape 不匹配)才回退到 Coding,
修复仍须 ADDITIVE、不破冻结面、不反向接错指标方向。
