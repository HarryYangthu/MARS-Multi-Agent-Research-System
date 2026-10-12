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




