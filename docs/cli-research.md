# 本地 CLI 研究闭环

这条路径不启动前端、Redis 或 API 服务。CLI → Bridge → 现有 NativeAgentLoop，复用 FocusedIdea 的真实论文阅读与跨模型审查；候选代码通过 Gate 5 和内容寻址工作区，交给独立 CPU 进程训练。

## 启动

使用 Python 3.11/3.12，Linux/macOS；Windows 请在 WSL2 中运行。原生 Windows 的源码快照权限语义尚未支持。

```bash
git clone https://github.com/HarryYangthu/MARS-Multi-Agent-Research-System.git mars
cd mars
git switch main
python -m venv .venv
source .venv/bin/activate
# Linux/WSL 使用 CPU wheel；macOS 跳过这一行，由下一行安装对应的 PyTorch。
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[pimc]'

# 私有仓库使用你自己的 GitHub 登录凭据。
git clone https://github.com/HarryYangthu/pimc-simulation-framework.git ../pimc-static
git -C ../pimc-static checkout 7b114051264f1b3780467576b434a15251c3af0a

# 在当前终端安全输入；也可配置 MARS 的 .env.local，不能提交 Key。
read -rsp 'DeepSeek API Key: ' DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY

mars doctor --repo ../pimc-static --data /你的路径/pim_16t_221110_38dBm_fr4_rnd32_1.pth --check-model
mars research --repo ../pimc-static --data /你的路径/pim_16t_221110_38dBm_fr4_rnd32_1.pth \
  --model deepseek-v4-flash --reduction 0.20 --max-degradation-db 0 \
  --max-steps 50 --rounds 3 --output ./runs/pimc20

mars status ./runs/pimc20
mars resume ./runs/pimc20
```

数据不在 Git 仓库中，必须提供真实 `.pth`，其中 `x/y/nf` 形状均为 `[16,time]`。它是用户信任的 PyTorch 文件，加载遵循上游静态仓的 `torch.load` 协议。CLI 不下载替代数据、不伪造实验结果。

上述步骤适用于包含本次 CLI 改动的主干版本。需要复现实验时，记录实际 `git rev-parse HEAD`，使用该提交重新创建运行；历史下载补丁只用于对应旧基点，不应重复应用到已包含这些改动的主干。

`--task` 接收自然语言背景；参数约束与计算预算以显式 CLI 参数和冻结协议为准。`--model` 选择 Coding/Analysis 模型，调研仍使用现有 `configs/idea_focused.yaml` 的 Pro 作者与 Flash 审查者；各 Agent 保留独立模型配置。研究命令启用公共文献检索并保存工具收据，默认来源域名见 `configs/cli_research.yaml`。

## 一次运行发生什么

1. 冻结外部源码、配置、数据 SHA-256、依赖版本、模型配置与预算，保存项目身份。先检查真实基线和数据结构，通过后才调用模型。
2. FocusedIdea 实际读代码、查论文、读方法，提出可证伪假设，接受另一模型审查。
   基线预检同时输出仅使用训练区间的功率、频谱与通道相关性诊断；真实原始形状、dtype、有限性、缩放和隔离区均有收据。诊断不是最终评分器。
   独立 Experiment Agent 在训练前基于上述证据生成 `experiment/plan.md`，确认冻结协议并解释对照、失败判据与本轮预算。协议仍由宿主执行，模型不能改写。
3. 相同评测协议训练原始 `StaticPIMC` 基线。
4. Coding 根据假设和已有实验生成完整 `build_model(config)` 模块；可重写候选架构，不局限于预定义参数网格。
5. Gate 5 审查后，候选只添加在隔离副本的 `libs/research_candidate.py`。原仓、原基线、评分器和数据划分不改动。
6. 真实检查参数量、两个输入长度、复数形状、有限值和反向梯度，再执行有预算的训练与验证。编译/参数/训练失败也进入反馈。
7. Analysis 解读实际验证结果，给出下一轮具体改动；下一轮 Coding 收到历史测量与上一轮分析。
8. 预算结束后，宿主按验证分数选择满足参数约束的候选，冻结选择，再分别对基线与该候选测试一次，生成报告。没有合格候选则明确失败。
9. 独立最终 Writing 调用读取已经冻结选择后的测试结果，生成 `writing/final.md`。测试后不再返回编码；后续研究必须另立协议及独立测试集。宿主另外从原始记录导出逐通道表、训练曲线、模型和工具调用表、Token 及失败汇总，缺失值保持缺失，不推算没有价格依据的费用。

`context/**/*.md` 中的领域背景可选加载，并与源码一起冻结；恢复会检查项目实际上下文的路径与内容。外部项目的原始 baseline 不改写。候选本地 Git commit 的父提交是冻结 baseline，`coding/*.patch` 和收据同时记录上游源码提交、冻结父提交、候选提交及差异哈希。

## 科学验收

基线实参数为 19,264（复数算两个实标量），减少至少 20% 对应整数上限 15,411。RES 直接调用外部仓库 `tools.train_static_compression.metric`，定义为 `10log10(mean_channel(Perr/Pnf))`，越低越好。训练过程使用同一个 Adam 实现、固定 seed、同一学习率/损失/划分/保护间隔和最大更新预算；按验证集选择 checkpoint。该适配器版本为 `pimc_static.cli.v1`，不同于上游命令逐方法立即测试的调度方式。

默认容忍退化为 **0 dB**；通过条件为实参数比不高于 0.8 且候选测试 RES 不高于基线。50 更新是有界的工作流与候选探索预算，不代表充分收敛，更不能证明多随机种子、不同采集条件下性能等价。报告明确保留此限制。若要做收敛验收，创建新运行并显式增大 `--max-steps`/时间预算，不能在旧运行中改变协议。

## 证据与恢复

`report.md` 汇总目标、预算、实际测量、判定和调研/代码/逐轮分析链接。`state.json` 可供其他 CLI 或未来编辑器插件查询。

| 路径 | 内容 |
|---|---|
| `input/manifest.json` | 固定输入、预算、源码快照与 Harness/配置哈希 |
| `experiment/protocol.json` | 评分、训练与数据身份 |
| `stages/` | 原生 Agent 调用、工具 Observation、审查与失败证据 |
| `idea/proposal.md` | 有来源的研究假设 |
| `coding/`、`candidates/`、`source_commits/` | 完整候选代码、Gate 5/内容寻址收据、执行前的本地 Git commit |
| `execution/*/attempt_*/` | 进程日志、每步 loss/梯度/学习率、逐轮验证、最优权重、结果、PID |
| `writing/` | 每轮基于实验的分析与下一假设 |

恢复复用已完成产物和测量；中断的 Agent 阶段或实验从该阶段重新开始，**不是优化器状态精确续训**。模型审查拒绝时，自动携带实际候选和审查反馈重试一次；仍必须重新通过审查。每阶段最多两次启动，旧 attempt 不覆盖。进程有独立墙钟上限，取消会终止进程组；文件锁防止同时恢复。论文调用预算沿用 FocusedIdea，代码/分析每次最多 12 个模型调用；配置或源码变化会拒绝恢复。

缺数据直接 `blocked_data`，CLI 返回码 2，且不调用模型。补齐数据后创建新 run，以固定真实数据哈希。只有显式 `--prepare-only` 可以在缺数据时做调研、代码准备和真实模型前向/梯度/参数检查，状态为 `prepared_only`，不计算训练或测试指标。失败返回码 1；`goal_not_met` 是正常完成但未达目标，见 JSON 状态。

候选进程不接收 API Key/SSH 凭据等环境变量。源码 lint、只读快照和独立进程用于防止常见越权与实验污染；它们不是针对恶意 Python 的 OS 沙箱。当前接口适用于可信本地研究仓，生产隔离可使用已有 Docker/remote 执行后端另行接入。

## 本次验证记录

开发环境为 Linux/Python 3.12，PyTorch 2.14.0+cpu、NumPy 2.3.5、SciPy 1.18.1。已通过 271 项相关检查、521 个 Python 文件的完整严格类型检查和 4 项依赖方向检查。

真实运行记录：Pro/Flash 调研完成 9 次模型调用、6 次工具调用、2 轮跨模型审查。首次代码路径暴露了正常 `__future__` 导入误拦与修复时上下文预算不足，均已修正。随后复用该真实调研作为参考重验代码，明确采用从头训练契约；第一次审查拒绝了不准确的初始化与数值偏差描述，携带实际反馈修正后，Flash 用 2 次模型调用完成生成与审查。

最终生成代码通过 Gate 5、隔离落盘、本地源码 commit、两种输入长度和真实反向梯度检查。宿主实数参数统计为 **19,264 → 14,960，减少 22.342%**，不是模型自报的数字。候选源码 SHA-256 为 `643ba097a77e51d34e42cb5c37cf1b01ebf1c83bf04f484526b91243e1c11265`，本地候选 commit 为 `5a35ae9514aa38fbdcd90ac7fcefcaff18ba1c4e`。

优化器、2 步预算、每步诊断、checkpoint 与独立测试阶段另外使用明确标注的解析张量 fixture 做了真实运算检查；这些测试不代表 RF 采集数据上的效果。阶段重试已接入候选和真实审查反馈，但上述真实重验包含开发期间的修复与阶段重跑，不是一次已完成的科学实验闭环。

**真实采集数据未提供，未完成 PIMC 训练或 20%/性能验收；当前会话没有连接用户 PC。** 参数上限与模型调用成功不能替代实测结果。

新增依赖：`filelock` 用于防止并发恢复；可选 `pimc` 依赖只用于本地 CPU 适配器。删除三个无调用的旧模块：`bridge/project_isolation.py`、`harness/context/compressor.py`、`storage/file_store.py`；其余旧流程暂不删除，避免在尚无真实数据回归的情况下破坏已有产品能力。
