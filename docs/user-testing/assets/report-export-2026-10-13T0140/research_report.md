---
schema: report.v1
project: folder_cde1b29d9e994bf4afc1ebdaa72cc13f
agent: writing
deliverable_type: research_report
target_audience: phd_advisor
chain_refs:
  plan: experiment/experiment_plan.approved.md
  code: coding/code_spec.approved.md
  runs:
  - execution/jobs/1c53a07ca93ad87a3937d46fcd9cd28c3306996ce8326474db4fc7211ed97232.json
  - execution/jobs/c90c8912d275e9fa77724fa5904012bc74ee1cbdcace24c1179e020da3ea6980.json
  - execution/jobs/dc6caca52e5f66fc8cec7ee969fdf7934b3e5f79c205bb17b43f3bad4f67cc92.json
  - execution/jobs/ec56d636ac0f6a70a1d2c886cb4a48275c7b6444f82a469632b3efb376733236.json
  - execution/local_commands/C_ca_pp_3term/a3fac473a8c647ebafa0b190b826e75a/evaluation.json
  - execution/local_commands/B_ca_pp_2term/1a9da08782ae44d3a1d3c8c8217945a9/evaluation.json
  - execution/local_commands/A_baseline_rerun/e3fedb7c1dbd4cff91a088e035226974/evaluation.json
  - execution/local_commands/D_ca_k1_control/9ab8f6b3522a42f895bdcf1ee362d009/evaluation.json
  - execution/metrics.json
  - execution/batch_summary.json
  - execution/loss_curves_16.png
  - execution/run_log.approved.md
  - diagnosis/diagnosis.v1.md
debate_summary:
  rounds: 0
  reviewer_critiques: []
---

# CA-PP 3 项（含带外镜像族）消融实验报告

## 0. 摘要

本报告检验唯一目标：在载波分辨 PP 族基中加入**带外镜像族 `[[0,2],[1,-1]]`（2c0−c1）及其频移 −162.494 MHz** 后，单频 group1 case17 的对消改善是否发生净变化。自变量仅为 `ca_terms` 项数（2 项 → 3 项），其余条件（数据、seed、预算、模型、`build_L1C:false`、`share_L30_across_terms:true` 等）逐字保持一致。

**结论**：本轮四臂（A 基线重跑 / B ca_pp_2term / C ca_pp_3term / D ca_k1_control）全部 `status=completed`，`failures=0`（`execution/batch_summary.json`）。处理臂 C `improvement_db = 17.20984`，相对对照臂 B 的 `17.18968` 净差值 **+0.02016 dB**（≥0），相对本轮同快照基线 A 的 `17.17516` 高出 +0.03468 dB；参数量实数等效量 **4704**（= 基线 6784 的 69.3% ≤ 80% 门限 5427.2）。因此在本轮同快照口径下 **C 同时满足**「参数量 ≤ 5427.2」与「improvement_db ≥ 合同门限 17.175」两项达标条件。

**必须同时声明的限制**：本结论基于**单 seed（42）单数据集（singleband_g1_c17）单 case（case17）单次运行**，净差值幅度（+0.02 dB 量级）与最大残差指标（C 的 `maxRES` 反而高于 B 约 +0.357 dB）之间存在方向不一致，不能外推为「带外镜像族普适有效」的结论，只能登记为「在本次设置下未观察到劣化且均值判据略优」的待验证假设。

---

## 1. 研究背景与可证伪假设

### 1.1 物理与建模背景

项目研究多通道基站无源互调（PIM）对消：由已知 TX 基带 IQ 预测落入 RX 带的 PIM 并减去（见项目 `README.md`）。本地单频参考场景的 TX 实为**双载波**（载波带宽 5/10 MHz，位于下行带内），因此即使在「单频段」语义下也存在载波间互调 `2c1−c0`、`2c0−c1` 等族基项。PP（物理先验）用于显式注入互调项的射频中心与相对 RX 的频移，把幅度非线性交给 LUT，把频率位置交给固定相位搬移（`README.md` 第 6 节）。

### 1.2 本轮唯一可证伪假设

在其余条件与上一轮完全一致时，把载波分辨 PP 族基由 **2 项**（0 阶全带锚点 `full` + 观察带内 IM3 族 `[[1,2],[0,-1]]`）扩展为 **3 项**（追加带外镜像族 `[[0,2],[1,-1]]`）后：

- **H1（参数量）**：`parameters_real_equivalent` 保持 4704 不变（相对基线 6784 的 69.3% ≤ 80%）。
- **H2（性能）**：`improvement_db` 相对 2 项对照的净差值 **≥ 0**（不劣化），且不劣于合同固定门限 17.175。

若净差值 < 0，则说明带外镜像族（其族中心位于观察带外）对带内 PIM 对消无正贡献或引入干扰——这正是本消融要判定的事实。

**来源**：`experiment/experiment_plan.approved.md` 第 1 节；合同 `task.v1` 达标口径。

---

## 2. 方法与实验设置

### 2.1 实际落地的改动（来自 `coding/code_spec.approved.md`）

净改动 3 个文件，均已在代码层面复核：

| 文件 | 类型 | 说明 |
|---|---|---|
| `configs/singleband_g1_c17_ca_pp_3term.yaml` | added | 由 2 项对照配置派生，仅改 `output`、追加 `pp_shifts_mhz: -162.494` 与 `ca_terms: {carriers: [[0,2],[1,-1]]}` |
| `tests/test_ca_pp_3term.py` | added | 4 个用例：与 2 项逐字差异、shift 一致性、模型激活与实数参数量 4704 与前向有限、shift 篡改抛 `ValueError` |
| `tests/test_mars_train_protocol.py` | modified | 在 `APPROVED_CONFIGS` 字典登记 `ca_pp_3term` 一行 |

未改动受保护路径 `baseline/`、评测器 `main.py::evaluate_saved`、训练入口 `tools/mars_train.py` 与模型 `libs/Model.py`。

### 2.2 族中心与 shift 自洽性（在代码层复核）

模型 `libs/Model.py::_parse_ca_config` 语义为：期望 shift = `ca_term_center_mhz(term) − f_rx`，与配置 `pp_shifts_mhz` 偏差超 `1e-3` 即抛 `ValueError`。据载波 `[[-9.799,4.927],[35.196,4.916]]`、`f_rx = 107.7`：

| ca_term | 族中心 (MHz) | 期望 shift | 配置值 | 观察带关系 |
|---|---:|---:|---:|---|
| `full` | 0 | −107.7 | −107.7 | 锚点 |
| `[[1,2],[0,-1]]` = 2c1−c0 = 80.191 | 80.191 | −27.509 | −27.509 | 落在观察带 −27.5±2.5 |
| `[[0,2],[1,-1]]` = 2c0−c1 = −54.794 | −54.794 | −162.494 | −162.494 | 带外镜像族 |

三项自洽，`tests/test_ca_pp_3term.py::test_three_term_shift_consistency_from_carriers` 在真实配置上复算通过。`configs/singleband_g1_c17_ca_pp_keepL1C.yaml` 仅作为第 3 项与 shift 的参考（其 `build_L1C:true`），不作本轮主配置。

### 2.3 变量控制与对照臂

- **自变量（唯一）**：`ca_terms` 项数 2 vs 3，差异仅在是否纳入带外镜像族与其 shift。
- **控制变量**（逐字一致）：数据 `singleband_g1_c17`，16 通道 `(16,30720)`；`seed=42`；`budget_steps=3588`；`build_L1C=false`；`share_L30_across_terms=true`；`pp_shift_mode=anchor`；`f_rx_mhz=107.7`；前两项 `pp_shifts_mhz=-107.7/-27.509`；模型 `MD7L_FCLNFC_2lut_tf`（`Ntx/Nrx/Nrank/Nbranch=16, Nspline=32, shareLUTs=1, batch_size=2048, circle_num=300, lr_init=1e-3`）；评测协议 `eval_centers_mhz=-27.5, BW_PIM=5.0, eval_trim=256`。
- **因变量**：主 `improvement_db`；次 `RES`、`maxRES`、`PER_THD`、`parameters_real_equivalent`。

### 2.4 四臂执行矩阵与实际运行状态

| 臂 | 名称 | config_path | seed | max_iters | 实际状态 |
|---|---|---|---:|---:|---|
| A | baseline_rerun | `configs/singleband_g1_c17_baseline.yaml` | 42 | 3588 | completed |
| B | ca_pp_2term | `configs/singleband_g1_c17_ca_pp.yaml` | 42 | 3588 | completed |
| C | ca_pp_3term | `configs/singleband_g1_c17_ca_pp_3term.yaml` | 42 | 3588 | completed |
| D | ca_k1_control | `configs/singleband_g1_c17_ca_k1.yaml` | 42 | 3588 | completed |

`execution/batch_summary.json` 报告 `total=4, failures=0, max_concurrency=1`，各臂 `attempt=1`。

### 2.5 基线与 K=1 对照的复用判定

按批准方案 `baseline_ref.reuse_decision = rerun`：由于上一轮已批准报告在本次运行上下文中不可获取（不是本 run 的可用文件）、`knowledge.baseline_match` 返回 `match_score=0.0 / reuse_recommended=false`，且读取元数据显示 `libs/Model.py` 等存在版本漂移，无法证实「同快照复用」，故 **A/B/D 与 C 在同一代码快照内重跑**以保证 C−B 与 C−基线比较的内部有效性。本轮**未复用**上一轮任何实测量；合同门限 6784 与 17.175 来自任务合同与代码断言，非本轮测量。

### 2.6 训练预算说明（重要口径）

配置矩阵名义预算为 `budget_steps=3588`（= `circle_samelayer × (circle_num − 1)` = 12 × 299）。但四臂实测 `optimizer_steps = 3289`（见 `execution/metrics.json`），由训练循环的边界批次裁剪决定。四臂在 `optimizer_steps` 上逐字一致（均为 3289），故 C−B 为同实际预算对照；报告以**实测 3289**为准，名义 3588 不等同于实测步数。

---

## 3. 实际结果

### 3.1 逐臂实测指标（来自 `execution/metrics.json` 与各臂 `evaluation.json`）

| 臂 | improvement_db | RES | maxRES | PER_THD | parameters_real_equivalent | active_parameters_real_equivalent | optimizer_steps | seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A baseline_rerun | 17.17516 | 5.24577 | 9.18777 | 18.69 | 6784 | 4704 | 3289 | 392.60 |
| B ca_pp_2term | 17.18968 | 5.23125 | 9.01284 | 18.71 | 4704 | 4704 | 3289 | 530.77 |
| C ca_pp_3term | **17.20984** | **5.21109** | 9.36964 | 18.74 | 4704 | 4704 | 3289 | 456.59 |
| D ca_k1_control | 17.17516 | 5.24577 | 9.18777 | 18.69 | 4704 | 4704 | 3289 | 349.58 |

指标方向与口径：`improvement_db` 越大越好；`RES`、`maxRES` 越小越好；`PER_THD` 为阈值口径统计量（越高越好，具体定义见评测器 `main.py::evaluate_saved`）；`parameters_real_equivalent` 为实数等效参数量（复数参数计 2）。`PIM = 22.420933` 四臂一致，`rx_rank = 12` 四臂一致，`covered_points = 24564 / declared_points = 24576` 四臂一致。

### 3.2 主比较：C（3 项）− B（2 项）净差值

| 指标 | B (2term) | C (3term) | 净差值 (C−B) | 方向 |
|---|---:|---:|---:|---|
| improvement_db | 17.18968 | 17.20984 | **+0.02016** | C 略优 |
| RES | 5.23125 | 5.21109 | −0.02016 | C 略优 |
| maxRES | 9.01284 | 9.36964 | **+0.35681** | C 劣于 B |
| PER_THD | 18.71 | 18.74 | +0.03 | C 略优 |
| parameters_real_equivalent | 4704 | 4704 | 0 | 不变 |

### 3.3 判读

- **H1 成立**：C 参数量 4704，与 B 相同；`share_L30_across_terms:true` 使 L30 维度 = R 与 Npp 无关，故 2/3 项参数量在结构上相等。4704/6784 = 69.3% ≤ 80%（5427.2）。
- **H2 在均值判据上成立**：C−B 净差 +0.02016 dB ≥ 0，且 C = 17.20984 ≥ 门限 17.175。
- **但方向不一致**：C 的 `maxRES = 9.36964` 是同组四臂中最大者，比 B 高 +0.35681 dB；而 D（K=1 对照）与 A（基线）在 improvement_db/RES/maxRES/PER_THD 上逐字一致（均 17.17516 / 5.24577 / 9.18777 / 18.69），说明 K=1 退化臂前向确与基线一致，D 的角色校验通过。
- **幅度评价**：本轮净差值 +0.02 dB 与其他臂间/臂内散布（如 A 与 C 的 maxRES 相差 0.18~0.36 dB）同量级，不足以在上述指标上支撑「带外镜像族带来实质性改善」的强结论；也无法排除单 seed 的运行变差。

---

## 4. 达标判定（沿用上一轮口径）

| 条件 | 门限 | C 实测 | 判定 |
|---|---|---|---|
| 参数量实数等效量 | ≤ 5427.2（= 6784 × 80%） | 4704 | **满足** |
| improvement_db | ≥ 17.175 | 17.20984 | **满足** |
| 3term 相对 2term 净差值 | 报告项（无门限） | +0.02016 dB | 已报告 |

诊断 `diagnosis/diagnosis.v1.md` 记录 `passed: true`、`confidence: 1.0`、`recommended_action: Proceed to Writing with the successful run evidence`。

---

## 5. 证据链与复现

| 环节 | 产物 | 引用 |
|---|---|---|
| 方案 | experiment_plan.v1 | `experiment/experiment_plan.approved.md` |
| 代码 | code_spec.v1 | `coding/code_spec.approved.md` |
| 执行清单 | run_log.v1 | `execution/run_log.approved.md` |
| A 收据 | evaluation.json | `execution/local_commands/A_baseline_rerun/e3fedb7c1dbd4cff91a088e035226974/evaluation.json` |
| B 收据 | evaluation.json | `execution/local_commands/B_ca_pp_2term/1a9da08782ae44d3a1d3c8c8217945a9/evaluation.json` |
| C 收据 | evaluation.json | `execution/local_commands/C_ca_pp_3term/a3fac473a8c647ebafa0b190b826e75a/evaluation.json` |
| D 收据 | evaluation.json | `execution/local_commands/D_ca_k1_control/9ab8f6b3522a42f895bdcf1ee362d009/evaluation.json` |
| 聚合指标 | metrics.json | `execution/metrics.json` |
| 批次汇总 | batch_summary.json | `execution/batch_summary.json` |
| 收敛曲线 | loss_curves_16.png | `execution/loss_curves_16.png` |

- 代码仓与分支：项目代码仓 `D:\a_documents\AGT\CDX\MARS_workspace\项目材料\baseline7L`（只读），实验分支 `mars/2026-10-13T0140_pimc_ca_pp_3term_g1c17-f33b9e4125c8`，基线分支 `mars/2026-10-11T1824_pimc_ca_sparsity_g1c17-f35e74ad6cc4`，基线提交 `b1fa4fd5d8d22d92ce015c295d8a1ebe40b1c03b`。
- 每臂输出目录（配置默认值与宿主实际作业目录以 `execution/job_files` 为准）：A `.../A_baseline_rerun/...`、B `.../B_ca_pp_2term/...`、C `.../C_ca_pp_3term/...`、D `.../D_ca_k1_control/...`，各自含 `evaluation.json`、`training.csv`、`validation_signals.npz`、`channel_performance.csv`、`config.json`、`runtime_evidence.json`、`execution_receipt.json`。
- **复现说明**：A/C 的 `training.csv` 哈希不同（`b0bce34...` vs `06660f1c...`）、`validation_signals.npz` 哈希不同（`ffbafe48...` vs `c747ef2e...`），说明两臂虽配置除族基项外逐字一致，训练轨迹与验证信号并非逐字节可复现——这与「不同 Npp 改变输入通道数」的机制一致（Npp=2 vs 3 使 TX 输入列数不同），因此参数量相同不代表计算图相同。

---

## 6. 局限、归因边界与下一步

### 6.1 已验证结果（可确证）

- 四臂均完成，无失败（`execution/batch_summary.json`，`failures=0`）。
- C 在同快照、同 seed、同实际预算口径下满足合同达标口径（参数量 4704 ≤ 5427.2；improvement_db 17.20984 ≥ 17.175）。
- C−B 净差值 +0.02016 dB，且 D（K=1）与 A（基线）四项指标逐字一致，佐证执行入口与基线一致性。
- 族中心/shift 三项自洽，`tests/test_ca_pp_3term.py` 在真实配置上通过（`coding/code_spec.approved.md` 报告 65 passed）。

### 6.2 待验证假设（不可据此断言必然改善或代码缺陷）

- 带外镜像族的正贡献幅度与显著性：单 seed、单 case 下 +0.02 dB 与指标散布同量级，且 `maxRES` 反向（C 更高）表明可能在个别通道/峰值上引入干扰。建议多 seed（≥3）重复 C 与 B，报告净差值的分布与置信区间。
- 带外镜像族与观察带内 PIM 的相位/幅度耦合机制，以及 `share_L30_across_terms` 共享映射是否限制了族基项的表达上限，均尚未通过消融逐一隔离。
- 未取得 `loss_curves_16.png` 的曲线标签与数值内容，故不宣称「单调收敛」；收敛性判断应引用 `training.csv` 逐 step 记录。

### 6.3 下一步所需验证

1. 多 seed（42/其他）重复 A/B/C/D，以区分真实效应与运行变差。
2. 增加「仅带外镜像族、去掉 IM3 带内族」的对照，判定镜像族信息的独立贡献。
3. 在 `channel_performance.csv` 层面核查 C 的 maxRES 抬升来自哪些通道/方向，确认是否需要重加权。

---

## 7. 结论

在本轮同代码快照、单 seed（42）、单数据集（singleband_g1_c17）、单 case（case17）、实测 3289 步预算的统一口径下：**把带外镜像族 `[[0,2],[1,-1]]` 纳入载波分辨 PP 族基（2 项 → 3 项）后，参数量实数等效量保持 4704 不变，improvement_db 相对 2 项对照净增 +0.02016 dB，达到合同门限 17.175（实测 17.20984），满足达标口径。** 与此同时，`maxRES` 出现 +0.357 dB 的反向变化，说明该结论仅适用于平均改善判据，峰残差维度未获益。该结果登记为支持假设 H1/H2 的单次测量，不构成「带外镜像族普适有效」的推广性结论。

---

*本报告的所有数值均来自上文 chain_refs 所列真实作业收据与聚合指标，未引用上一轮任何未证实实测量。数据与 Memory 材料仅作背景参考，不作为实验证据。*

## 已保存的关键图片

![已记录的实验图](images/b067974f2fc0-loss_curves_16.png)

来源：`execution/loss_curves_16.png` · SHA-256：`b067974f2fc02be017f0fe819bd204bbb6f518985d0447a9331dea6a1d8f63d4`
