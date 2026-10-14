# Report Prompt

读取当前项目合同、项目规则、proposal、experiment plan、code spec、真实 execution 记录和 HITL 记录，产出 `report.v1`（YAML frontmatter + Markdown body）。

## 输出重点

- 摘要说明研究目标、实际完成范围及目标是否达到。完成流程与达到目标分开；缺少必要信息时明确阻断对应结论，并列出证据缺口。
- 方法与实验设置来自实际代码、数据、配置和上游产物，说明对照、划分、seed、预算、实现版本及所用环境。不能照搬其他项目的方法、数据格式或默认常数。
- 逐实验报告真实指标、单位、方向、目标、容差和汇总方式。只有这些定义和比较协议明确且一致时才能排名或判断达标；方向未知时只列测量，不猜“最佳”。
- 区分待执行计划与运行结果。`execution_phase=planned`、批准文件、schema 校验、退出码或可见面板均不能替代真实测量与作业收据。
- 全文使用 `chain_refs` 指向实际产物、run/attempt/job、日志和测量来源。摘要统计不能替代逐项证据，也不能掩盖失败、无效或缺失结果。
- 保留证据文件完整后缀，不假定训练记录一定是 steps.jsonl 或配置一定是 config.yaml。执行作业可能实际提供 steps.csv、history.json、config.json；按 execution.job_files 中宿主校验过的 source 与 SHA 引用真实路径。配置中的 output_dir 不能代替实际输出目录。
- execution.job_files 中有 material_ref 的文本证据可用 context.read_material 分页读取；它指向已校验的当前文件快照。先读取当前 manifest、有效配置、summary 和训练记录，再写指标公式、聚合、学习率、预算和选模规则。旧 README 或其他接口中的常数、钳位规则不能自动套用到当前执行实现；出现冲突时核对当前代码的具体函数及本次代码版本，没有证据就明确未知。
- 曲线面板的数量和横轴单位必须来自实际记录，文件名含有 16 不证明它展示 16 个通道。单个 loss_curve 可能是验证评估点，不一定是逐 optimizer step 或每轮 epoch；以真实 CSV/history 的字段对齐。不宣称未被逐步记录支持的单调下降、收敛或统计显著。
- execution.job_files 是上下文材料标签，不是磁盘文件。chain_refs 应逐字引用材料中 source 字段的真实文件路径，不要创造 execution/job_files 这样的伪路径。
- 先使用 admitted_report_sources 中宿主核对的路径清单。任务从编码或执行阶段进入时，proposal/plan 可能不存在；chain_refs 的这些字段可省略，不得拿编码产物替代提案或凭空补写文件路径。
- 归因只写证据支持的范围；区分已验证结果、负结果、无效实验和待验证假设。单次 seed 或小规模运行不能自动推广。
- 如实说明 baseline 保护、数据使用边界、人工决定、失败与迭代记录，以及下一步所需验证。不得承诺某种参数或实现改动必然改善。
- 重试、自动修复和反馈材料不等于用户拒稿。human_revision_request 是兼容材料标签，不能单凭标签判断反馈来源；只有真实 HITL 拒绝记录才可称用户拒绝。只有真实 debate 调用/转录才可称辩论评审，不把重复尝试写成“经多轮专家辩论”。
- 作业文件数量或排序不代表 attempt 编号；按宿主记录的 attempt 与 updated_at 说明阶段尝试和时间，不把第三条记录编造成 attempt 3。辩论关闭时省略 debate_summary，或明确 rounds=0、reviewer_critiques=[]。
