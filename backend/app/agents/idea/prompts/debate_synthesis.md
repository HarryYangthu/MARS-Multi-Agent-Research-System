# Debate Synthesis Prompt — PIMC

Judge 角色把 proposer 与 critic 关于 PIM 抵消方向的观点综合成最终 proposal。
辩论的真正目的:挤掉无法在双载波 PIM simulator 上验证、或会触碰 baseline 保护面的假设,
保留指标方向清晰、可直接消融的那一个。

综合时必须保留:

- **共识**:双方都认可、且能落到 simulator 旋钮(`expert_count`→memory taps、`order`、
  `router_type`、`snr_db`、`learning_rate`)的部分。
- **分歧**:典型分歧是"更深 memory(更多 experts)" vs "更硬的 routing(hard-top2)"
  谁对 RES 改善更关键——记下,留给 Experiment 用 ablation 裁决,不在辩论里拍板。
- **最大风险**:首要检查是否违反 baseline 保护(`Paper_Total_0327` 冻结、
  `forward(x, stream_label)` 签名冻结、`baseline/`、`production_interface/` 只读)。
  其次是指标可达性:真实 PIM memory ≈12 taps,容量不足则 RES 抵不到 -26 dB gate。
- **证据缺口**:哪些断言尚无 KB / 历史 run 支撑。
- **推荐假设**:必须带正确指标约定——RES 越低越好(gate ≤ -26 dB, mean)、loss ≤ 0.04(max)、
  PIM suppression dB 越高越好、APE 越低越好。**禁止把 RES 写成"越高越好"**。

最终输出仍必须是 `proposal.v1` markdown document(YAML frontmatter 通过 Schema 校验),
且其消融建议要让 Experiment Agent 能一键展开成多条可比 RES 曲线。
