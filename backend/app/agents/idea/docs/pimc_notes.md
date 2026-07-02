# PIMC 领域注意事项

PIMC 项目的 Idea 判断重点：

- 保持 `forward(x, stream_label)` 接口兼容。
- 不修改 `Paper_Total_0327` baseline 保护面。
- 假设需要能落到 RES / PIM / APE / loss 等可观测指标。
- 资源降低类假设要同时说明计算量、专家数、memory taps 或 routing 复杂度的变化。
- 涉及 ASIC / 硬件资源时，要给出可近似验证的实验 proxy。
