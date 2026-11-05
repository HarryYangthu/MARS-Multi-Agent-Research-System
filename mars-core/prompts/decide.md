你负责本项目进化中的“分析与决策”。下面是本路线的已批准方案、目标、历史轮次，以及本轮由程序实测得到的证据（所有数字都来自真实评测和项目检查，不是估计）。请判断机制是否得到支持，并决定下一步。

# 已批准方案
{{proposal}}

# 目标与硬约束
{{targets}}
{{constraints}}

# 领域说明（项目提供）
{{domain}}

# 指标定义（better 表示方向；worse_by = 候选比基线差多少，负数表示更好）
{{metrics}}

# 项目可用的诊断
{{diagnostics}}

# 项目背景与历史结论
{{lessons}}

# 本路线历史（每轮改动与关键数字）
{{history}}

# 本轮记录（任务、分支、改动文件、实测证据）
{{record}}

# 本轮编码过程（各轮检查结果）
{{implementation}}

# 相关项目知识
{{kb}}

# 操作者留言（尚未回应，按时间先后）
{{notes}}

# 证据说明
- checks / check_gates：项目检查在训练前的输出，以及研究约定中检查门槛的判断；stop 门槛不满足时不训练。
- prequick：短预算评测与其门槛，用于尽早发现不学习的候选。
- screen：各数据集在筛选预算下的指标；主指标与多种子基线的中位数比较，margin 为噪声门槛；worse_by 为候选比基线差多少。
- auto_diagnostics / diagnostics：项目诊断的输出。
- verify：完整预算下与完整基线的对比，按研究约定的指标门槛判断，这才是验收标准；screen 只是代理，两者排名可能翻转，verify 存在时以 verify 为准。
- stopped_at：在哪一级被拦下（implementation / checks / prequick），说明还没有更高一级的数据。

# 项目对证据的补充说明
{{domain_decide}}

# 要求
1. mechanism_assessment：结合证据判断方案的机制假设是否得到支持；区分事实（程序实测）与推断。
2. evidence_for / evidence_against：分别列出支持和反对的实测事实（引用具体数字）。
3. alternative_explanations：除机制本身外，还有哪些可能解释（例如训练不足、初始化、超参数、只在部分数据或部分指标上改善）。
4. verdict：
   - continue：路线仍有支持，给出下一轮改动；
   - promote：筛选已过、建议完整验收（系统会在门槛满足时自动验收）；
   - refuted：关键机制被实测否定（连续两次将终止本路线，回到 Step1 重新提方案）；
   - stop：已达目标或继续无意义。
5. next_change（continue 时必填）：每轮只做一个主要改动，必须能区分上面的不同解释；implementation_spec 要具体；expected_observation 写清预期会在哪个指标上看到什么变化。若本轮编码失败或被检查门槛拦下，下一轮应先修正实现。剩余可用轮数：{{remaining}}。需要先测量再决定的问题写进 next_change.diagnostics（从项目可用的诊断中选择，最多 2 条），系统会在下一轮 prequick 前执行；只写在 implementation_spec 里不会被执行。本轮 record.evidence 中若有诊断结果，必须在判断中使用。
6. operator_notes：按上面留言的顺序逐条给出 {"note": 留言原文, "adopted": true/false, "reason": 理由}；采纳的要体现在 next_change 里，不采纳要说明依据（例如与实测证据或硬约束冲突）。没有留言时为空列表。
