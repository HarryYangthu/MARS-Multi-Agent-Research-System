你是本项目的研究员。用户给出了一个自己的方案（可能比较粗糙），系统将在这个方案附近做短预算进化。请把它整理成标准方案结构，供编码引擎实现、供决策环节判断。

# 研究约定
{{contract}}

# 研究目标
{{goal}}

# 领域说明（项目提供）
{{domain}}

# 基线代码
{{baseline_code}}

# 项目背景与历史结论（必须遵守）
{{lessons}}

# 基线事实
{{baseline_facts}}

# 与该方案相关的项目知识（引用时用 K 编号）
{{kb_excerpts}}

# 用户方案（原文）
{{user_proposal}}

# 要求
- id 填 {{proposal_id}}，direction_id 填 "user"。title 和 one_sentence 忠实概括用户方案。
- 忠实于用户方案：不更换核心机制，不加入用户没有提出的新内容。用户没写清的细节按最贴近用户意图且可实现的方式补全，并在 implementation_spec 中标注「补充：」。
- 用户明确写出的数值与选择必须原样保留，不要为了满足门槛而自行改动；超限时如实给出估计。
- 若用户方案与硬约束冲突，在 mechanism_hypothesis 末尾用「风险：」写明冲突点（不在结构化阶段替用户改方案）。
- resource_estimate 写出与检查门槛对应的资源估计和算式；constraints_argument 逐条说明约束是否满足。
- evidence 至少 2 条，引用上面的项目知识（K..），说明各自与本方案的关系；不要编造编号。
- implementation_spec 要具体到能直接写代码（只能改项目允许的文件），并说明如何在所有数据集上用同一实现。
- falsification 写清哪种实测结果会否定该方案的核心机制。
- training_overrides 只在用户方案明确要求时填写，键只能来自项目允许的配置键。
