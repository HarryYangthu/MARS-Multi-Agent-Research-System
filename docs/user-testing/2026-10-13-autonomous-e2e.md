# 2026-10-13 自动化端到端运行与体验优化记录

> 目标：模拟真实用户跑通 MARS 完整流程（Idea → Experiment → Coding → Execution → Writing），
> 从用户视角发现并修复体验问题，产出可汇报的真实结果。
> 运行方式：ZCode 自主会话（用户授权自动化执行）。本文件为持续追加的工作日志。

## 环境快照（会话开始）

- 代码基线：`MARS-upstream-20261011`，HEAD `760cd4f`（Keep commander execution checks off event loop and allow slow local reads）
- 服务：后端 http://127.0.0.1:8010 （健康 ok），前端 http://127.0.0.1:3001 （307 正常跳转）
- 模型：zhipu (glm-5.3) + deepseek 已配置（.env.local）
- 执行后端：local_command（CPU），训练脚本 `MARS_workspace\项目材料\baseline7L\tools\mars_train.py`
- 项目：`folder_cde1b29d9e994bf4afc1ebdaa72cc13f` → `MARS_workspace\test`；readiness 全部通过
- 历史 run：
  - `2026-10-10T0710`（coding 入口，folder_9d4d…）
  - `2026-10-10T0726`（pipeline，folder_cde1…）
  - `2026-10-11T1824_pimc_ca_sparsity_g1c17`：execution 因 **Windows lock deadlock** 失败，
    之后 git 已有 4 个相关修复（f23e37d/4429446/760cd4f/de9759e）。recovery API 显示
    `retry:execution` 可用，交接资料绑定已核验。

## 计划

1. [进行中] 重试 `2026-10-11T1824` 的 execution（验证锁修复、真实训练），期待推进到 writing 完成
2. 以新用户视角在 UI 新建完整 pipeline 任务，逐 HITL 审核推进
3. 全程记录体验问题 → 修复 → 复验
4. 汇总可汇报结果（报告、数据、证据）

## 日志

### 02:30 启动
- `Mars.ps1 -Action Start` 首次因 `api-configured.json` 缺失进入交互式 API 向导，非交互 EOF 失败。
  **[体验问题 #1]**（已用 `-SkipConfigure` 绕过）：向导在无人值守/CI 场景无 `--skip` 默认值兜底；建议缺失时若 `.env.local` 已有 Key 则跳过向导并提示。
- 02:35 后端/前端均监听成功，`/health` ok。

### 02:40 项目页与恢复入口（UI 体验）
- 项目列表页：两个 folder 项目都叫 "test"，下拉框出现两个同名 "test" 选项，无法区分。
  **[体验问题 #2]** 项目显示名重复时无区分后缀（如路径或创建时间）。
- 研究以对话（chat）组织，`主控托管` 开关默认开。对话内 HITL 提问/回答完整可回放，体验良好。
- 失败 run 在对话底部出现「任务恢复 → 重试当前阶段 · 执行」按钮，点击后立即重建执行节点工作区。✅

### 02:45-02:50 重试 execution（关键验证：Windows 锁死锁修复）
- 点击重试后，主控 managed review 先跑，判定 `needs_user`（基线重跑缺 evaluation.json 实测分母），
  但**该判定未推送新消息到对话**，用户只能从侧栏状态变化（等待核对仿真配置）察觉。
  **[体验问题 #3]** retry → needs_user 决策没有在 chat 里生成对应消息/提醒。
- 「核对仿真配置」对话框信息完整：3 组实验（baseline_rerun / ca_k1_degenerate_control / ca_pp_2term）、
  每组 3588 次参数更新、分支 mars/2026-10-11T1824...、真实作业记录（上次中断原因）都在。
  勾选「我已核对…」→「确认配置，启动仿真」后 confirmed=true。
  小问题：点击确认后按钮变「正在确认」但对话框停留较久（~30s），无进度提示。
  **[体验问题 #4]** 确认启动期间缺少即时反馈（loading 文案有了，但预计耗时未知）。
- 02:49 真实训练进程启动（mars_train.py，PID 30912/5364），`baseline_rerun` 状态 running。
  新 attempt 目录 bd7d57ba…，training.csv 增长正常（circle 100/updates 1100，RES 21.38→5.54dB）。
  节奏 ≈ 每实验 8-9 分钟，3 实验串行预计 ~30 分钟。**Windows 锁死锁修复在真实启动路径上初验证通过**（待全程完成确认）。

### 02:50-03:00 执行监控与实验台体验
- 实验台（/lab）实时展示训练曲线：实际更新数、最新损失、训练 RES、损失收敛图、对消前后带内功率图，
  且同时列出历史中断 attempt（a33642c4）与新 attempt（bd7d57ba），证据保留完整。✅ 截图存 assets/lab-live-training-0252.png。
- 02:57 `baseline_rerun` **completed**（267s CPU，3289 更新）→ 全程无锁死锁复现，修复确认。
  随即 ca_k1_degenerate_control 自动开跑（并发=1 串行调度正常）。
- 结果页（/results/...）中途可访问：任务问题、目标判定状态、真实指标表、事实/假设分离、局限说明。
  注意点：结果页导航有一次超时（Next dev 首次编译慢），非功能性。

### 03:00-03:15 三组实验全部完成（关键结果）
| 实验 | RES(dB) | improvement_db | 参数量 | CPU 秒 |
|---|---|---|---|---|
| baseline_rerun | 5.246 | 17.175 | 6784 | 267 |
| ca_k1_degenerate_control | 5.246 | 17.175 | 4704 | 485 |
| ca_pp_2term（候选） | **5.231** | **17.190** | **4704** | 327 |

- **目标判定：参数量 4704/6784 = 69.3% ≤ 80% 达标，且性能不劣于基线（RES 略优 0.015dB）。**
- K=1 对照与基线完全同分（5.246/17.175）→ 证明「共享 L30 + 关闭单频未用的 L1C」本身无损，
  载波分辨族基项的净效果 = 5.246−5.231 = +0.015dB（小而正确为正）。
- 执行节点 done → **writing 节点自动开始**（stop_after_execution=false 生效）。

### 03:15-03:20 writing 完成与最终报告
- writing 经主控托管审核自动通过（waiting_review → done），run 终态 **completed**。
- 最终报告 `writing/research_report.approved.md`（schema report.v1）质量高：
  目标判定诚实（分支 A 达标：17.190≥17.175 且 4704≤5427.2；分支 B 不成立：+0.0145dB<2dB），
  明确降参归因（关闭单频未用的 L1C），载波分辨项净贡献 +0.0145dB 如实报告为"小而正确为正"，
  边界/缺口/证据索引完整（单种子、PER_THD 边缘劣化、covered_points 差 12 点均列出）。

### 03:20-03:45 体验问题修复（已提交 1c73390）
| # | 问题 | 修复 | 验证 |
|---|---|---|---|
| 1 | 无 `api-configured.json` 时启动进交互向导，无人值守 EOF 崩溃 | `Mars.ps1` Start：`.env.local` 已有 `*_API_KEY=` 非空行时跳过向导并提示 | 不带 `-SkipConfigure` 重启成功 ✅ |
| 2 | 两个项目同名 "test"，下拉框无法区分 | ProjectSwitcher 同名时追加文件夹路径尾段 | 显示 `test（20261010项目一/test）`/`test（MARS_workspace/test）` ✅ |
| 3 | retry → needs_user 决策不进对话，用户无感 | `managed_review.record_state` 统一挂钩：decision/status=needs_user 时向关联对话追加 system 通知 | mypy/单测通过；待真实事件复验 |
| 5 | run 完成/失败对话零通知（本次真实踩坑） | orchestrator 终态处 `_notify_run_completion` → system 通知 | 真实通知写入并渲染 ✅ |
| — | 前端过滤 system 消息导致通知不可见 | system 渲染为居中状态行（role=status） | DOM 验证 ✅ |

- 新增项目判定准则 `.mars/diagnostics.yaml`（test 项目）：improvement_db≥17 mean / RES≤5.5 mean /
  parameters_real_equivalent≤5427.2 min。对已完成 run 复核：evaluated=True **passed=True**。
  该准则使后续 run 的诊断与结果判定有据可依（本 run 完成于准则配置前，报告以任务合同口径判定，诚实且达标）。
- 检查：mypy --strict ✅、tsc --noEmit ✅、相关单测 ✅、PS 语法 ✅。
- 备注：CommanderSessionStore 内存优先于磁盘——外部进程写 messages.jsonl 会被运行中后端的内存态遮蔽，
  进程内 hook（本次实现方式）不受影响；这是既有设计，记录备查。

### 03:45 起：全新完整流程验证（新对话 → Idea → … → Writing）
以报告建议的后续方向为任务，验证（a）新用户完整流程体验（b）新通知钩子在真实事件中生效。

### 03:30-03:55 新对话首发踩坑 → 体验问题 #6（已修复 6bc5a17）
- 新建对话（主控托管默认**关**，需手动开——记录为观察点，不算缺陷：新用户首跑会走人工审批更稳妥）。
- 提交任务后总控第一次调用正常（JSON 决策 + repo_list/repo_search），**第二次调用 deepseek-flash
  返回 DeepSeek 原生 `<｜｜DSML｜｜ calls>` 工具调用标记而非 JSON**，`_parse_decision` 抛
  CommanderDecisionError → 409 invalid_commander_decision，回合中断，需用户手动发"继续"。
- **修复**：`_parse_decision` 增加 DSML 兼容解析（忠实解析模型实际输出的工具意图：typed 参数、
  前置散文作 reply；残缺标记仍抛错，不伪造决策）。新增 3 个单测；mypy/测试通过。
- 后端重启后发"继续"恢复：总控完整走完（读配置 → 核对第 3 项族基数学自洽 → 以 **experiment 入口**
  创建 run `2026-10-13T0140_pimc_ca_pp_3term_g1c17`，跳过 idea 文献调研——针对后续消融任务的合理省算力决策）。

### 04:00-04:15 HITL 闸口与审核界面（体验问题 #7 记录）
- experiment 产物 waiting_review，主控审核判 needs_user（3term 配置不存在、测试仅断言 2 项——真实硬阻断）。
- **#3 修复真实验证 ✅**：「【需要你决策】实验设计 · 产物审核 已暂停…」系统消息在判定同一时刻（01:43:44）进入对话。
- 用户在对话里给出决策（配置由 coding 交付、基线/K1 同快照重跑、批准放行）后，主控回答"无法代你放行，
  approve_node/user.approval 均被拦截，请在审核界面接管"。
  **[体验问题 #7]**：托管 needs_user 后，聊天中的明确用户决策不能直接放行，必须再去审核界面点一次「批准并继续」——
  两步冗余；且主控两次尝试（一次失败 tool 调用）才说清。建议：用户在对话中的明确批准语义可绑定到
  needs_user 闸口（或至少审核界面直接给出跳转/快捷批准入口）。（未在本轮修复，记录为后续项）
- 在对话页底部工作区点「批准并继续」→ experiment done，coding 自动推进。

### 04:15-04:25 coding 完成（交付真实）
- coding 自动通过主控审核（这次无硬阻断——前置条件齐备，托管审核设计意图的正确体现）。
- 研究仓新 commit `285d39c`：`configs/singleband_g1_c17_ca_pp_3term.yaml`（74 行，含族中心数学注释：
  2c1−c0=80.191→−27.509；2c0−c1=−54.794→−162.494）+ `tests/test_ca_pp_3term.py`（115 行）。

### 04:25 执行确认与训练启动
- 「核对仿真配置」对话框：4 组实验（A 基线 / B ca_pp_2term / C ca_pp_3term / D K1 对照），每组 3588 更新，
  累计模型请求 30 次透明展示。确认后 A_baseline_rerun 训练真实启动。
- 预计 4×~8 分钟串行完成，随后 writing。

### 04:30-05:30 四臂训练全部完成（第二轮真实结果）
| 实验 | improvement_db | RES | maxRES | PER_THD | 参数量 | CPU秒 |
|---|---|---|---|---|---|---|
| A baseline_rerun | 17.17516 | 5.24577 | 9.18777 | 18.69 | 6784 | 393 |
| B ca_pp_2term | 17.18968 | 5.23125 | 9.01284 | 18.71 | 4704 | 531 |
| **C ca_pp_3term** | **17.20984** | **5.21109** | 9.36964 | 18.74 | 4704 | 457 |
| D ca_k1_control | 17.17516 | 5.24577 | 9.18777 | 18.69 | 4704 | 350 |

- **C 相对 B 净增 +0.0202dB，相对基线 +0.0347dB，参数量保持 4704（69.3%）→ 双条件达标**。
- D 与 A 逐字一致（K=1 退化对照前向等同基线的角色校验通过）。
- 诚实结论保留在报告中：maxRES 反向 +0.357dB，结论仅适用于平均判据。

### 05:30-05:55 writing 审核发现产品级证据链缺陷（已修复 9ea2769）
- writing 报告数值与四臂 evaluation.json 完全一致（逐项核对通过），但主控审核判 needs_user：
  「run_log.approved.md 状态 interrupted/planned，材料中未提供 evaluation.json/metrics.json 真实内容」。
- 根因：run_log.approved.md 是**批准时点冻结的计划**，作业完成后不回填；writing 审核材料只含上游
  approved 产物，不含执行收据 → 审核者无法核验报告数值（第一轮通过纯属报告正文恰好引用了实测内容）。
- **修复**：writing 产物审核 payload 附上与 Writing Agent 相同的执行收据 handoff
  （metrics/batch 摘要、作业回执、曲线说明）。mypy/测试通过，已提交。
- 人工核对四臂数值真实后批准 writing → **run completed（10:52）**。
- **#5 修复真实验证 ✅**：完成通知在终态同秒（02:52:53 UTC）进入对话。
- 全轮 needs_user 三次通知（experiment 产物 / 执行配置 / writing 产物）全部即时到达对话 ✅。

### 05:55 收尾
- 新 run 结果导出包：assets/results-export-2026-10-13T0140.zip（24363 字节，SHA256 清单）。
- 项目准则对两个 run 复核：evaluated=True, **passed=True**。
- 最终结果页截图：assets/results-final-2026-10-13T0140.png。







