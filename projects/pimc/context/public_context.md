# PIMC 公共上下文

## 项目背景

PIMC 研究多通道无线系统中的无源互调抵消。发射链路和无源器件的非线性会在接收端形成 PIM 干扰；项目目标是用可训练的 canceller 从接收信号中估计并抵消 PIM，使 residual 尽可能接近 noise floor。

所有 Agent 的产物都要能回到真实代码、真实配置和真实指标中验证，不做脱离代码入口的泛泛方案。

## 真实来源

- 研究代码：`/Users/harry/Documents/20_paper/code`
- MARS 项目配置：`projects/pimc/repo_link.yaml`
- 当前执行后端：`paper_static`
- Python：`/opt/anaconda3/bin/python`
- 当前入口：`train_static.py --cfg configs/static.yaml`
- 当前默认数据配置：`/Users/harry/Documents/20_paper/data/static/pim_16t_221110_38dBm_fr4_rnd32_1.pth`

真实代码和真实数据不复制进 MARS 仓库；MARS 只通过路径连接它们，run 输出写入 `runs/<run_id>/`。

## 代码与数据约定

- `configs/static.yaml` 是当前激活静态场景的配置来源，包括数据路径、通道数、采样率、PIM 统计频带和模型默认值。
- 数据格式和通道数不是项目常量。真实代码支持 `.pth` / `.pt` / `.mat`；通道数由配置和数据决定。
- 当前默认样例加载结果为 `x/y/nf` 三个数组，形状均为 `(16, 196608)`；这只是当前默认样例，不是项目边界。
- 真实数据加载逻辑在 `libs/data.py`，训练逻辑在 `train_static.py`、`libs/engine.py`，指标逻辑在 `libs/metrics.py`。

## 指标语义

以真实代码 `libs/metrics.py` 为准：

- `PIM`：接收端 PIM 功率相对 noise floor 的 dB 值。
- `paper_RES_db`：抵消后 residual error 相对 noise floor 的 dB 值，越低越好。
- `paper_APE_db`：抵消改善量 / cancellation gain，单位 dB，越高越好；不是角度或相位误差。

为兼容 MARS 旧诊断 gate，`paper_static` adapter 还会写：

- `RES = -paper_APE_db`，越低 / 越负越好。
- `loss = 10 ** (-paper_APE_db / 10)`，越小越好。

报告必须同时保留原始论文指标和 MARS 兼容指标，不能混用含义。

## Baseline 保护

- 受保护 baseline：`libs/model.py:Paper_Total_0327`
- 动态接口 `forward(x, stream_label)` 继续受保护。
- `baseline/**`、`production_interface/**` 对 MARS 只读。
- 除非人工明确批准，代码改动必须 additive，并给出测试和回滚说明。

## 通用规则

- 遇到旧 mock 文档与真实代码冲突时，以当前激活的真实配置和真实代码为准。
- 不要把当前默认 `.pth`、16 通道、`fs=245.76 MHz` 写成项目永久限制。
- 不要把 `paper_APE_db` 写成 degree 或相位误差。
- 不要提出真实入口或 adapter 没有消费的参数。
