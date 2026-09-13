# 独立运行 Idea Agent

`scripts/run_idea_standalone.py` 只注册并运行 Idea，经过正常 Bridge 和原生模型/工具循环。无需启动网页、Commander 对话或后续 Experiment / Coding / Execution / Writing Agent。文献调研子 Agent 仍按原配置执行；独立模式保留原有来源、Schema、材料和审查要求。

将研究需求保存到 UTF-8 文本文件，再在仓库根目录运行：

```sh
PYTHONPATH=.:backend:posttrain/src .venv/bin/python scripts/run_idea_standalone.py \
  --project pimc --request-file /absolute/path/request.md --max-seconds 1500
```

默认 `project_proposal` 面向项目优化。仅研究符号方法时，显式指定 `--scope method_proposal`。如需提供已知基线或数据说明，用 `--context-json /absolute/path/context.json`；内容为标签到原文的映射，支持 `background`、`baseline_code`、`data_description`、`analysis_results`、`metric_definition`、`literature_notes`。CLI 保留原文并按既有合同存档，不把链接、文件名或模型记忆当作已读取的基线。

`--requirements-json` 接受 API 同款 IdeaRequirements；未提供时保留原门槛。模型和工具使用 `configs/agents.yaml` 与显式设置的 `MARS_IDEA_RUNTIME_PROFILE`。凭证通过既有环境变量或本地被忽略的 `.env.local` 提供，不放进需求或上下文文件。需要联网检索时仍需配置正常的网络工具开关和来源域名。

加 `--prepare-only` 可以先检查并保存输入。该模式不会调用模型或工具，也不会生成方案。正常执行前检查 Idea 和研究子 Agent 的模型配置；配置存在只表明可以尝试请求，不证明账户余额或服务可用。

每次运行记录在独立的 `runs/<timestamp>_<task>/`，入口输出确切路径：

- `input/user_request.md` 和输入上下文：原始需求。
- `agent_traces/`：真实模型请求、工具观察、候选与审查。
- `idea/idea_proposal.v1.md`：通过既有验收后生成的待审阅方案。
- `idea/standalone_summary.json`：状态、源码提交、诊断、产物路径和进度计数。
- `events/agent_events.jsonl`：状态变更和安全错误诊断。

退出码 `0` 在执行模式表示方案已经到达 `waiting_review`；在准备模式只表示输入已保存。`1` 表示执行失败或超过时间限制；`2` 表示输入或配置错误；`130` 表示用户中断。待审阅不代表人工批准或性能已验证。CLI 用已有的停止操作结束自身的人工审阅等待，保留待审阅状态和材料，并记录 `standalone_review_ready`，不自动批准或运行实验。之后可在使用同一 runs 目录的工作台重新载入该任务审阅。

时间限制会停止当前进程拥有的真实工作并记录清理状态；停止不完整会显式报告。模型调用状态未知时，不自动重放、续跑或伪造成功。

API 同样支持独立模式：创建时设置 `entrypoint: "idea"`、`standalone: true`、`auto_approve: false`，随后调用该任务的启动接口。生产模式只对这类请求检查 Idea 与研究子 Agent 的模型配置，保留项目、模板、Gate 检查，跳过无关执行环境检查。完整流水线仍执行全部检查。

HTTP 402 会保留 `provider_payment_required` 和中文处理提示。账户恢复后新建任务重试；修改代码不会消除服务端余额限制。失败运行没有方案时，不能把空的评估统计当作 Idea 已通过。
