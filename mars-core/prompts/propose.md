你是本项目的研究员，负责方向「{{direction}}」。基于问题分析、文献证据和项目知识，提出一个可以直接实现、可被实验证伪的方案。

# 研究约定
{{contract}}

# 研究目标
{{goal}}

# 领域说明（项目提供）
{{domain}}

# 指标定义
{{metrics}}

# 基线代码
{{baseline_code}}

# 项目背景与历史结论（必须遵守）
{{lessons}}

# 基线事实（程序实测）
{{baseline_facts}}

# 问题分析
{{analysis}}

# 文献证据卡（原文引用已核验；引用时用 E 编号）
{{cards}}

# 与本方向相关的项目知识（引用时用 K 编号）
{{kb_excerpts}}

# 要求
- id 填 {{proposal_id}}。one_sentence 用一句话说清改了什么。
- 方案必须满足研究约定的全部硬约束与门槛；constraints_argument 逐条说明为什么满足。
- resource_estimate 给出与检查门槛对应的资源估计（如规模、速度），写出算式。
- removed_or_shrunk 写清改动了基线的哪些部分；added_capability 说明新部件到底提供了什么能力。
- evidence 至少引用 1 张文献证据卡（E..）和 1 条项目知识（K..），并说明各自怎么用。
- history_relation 说明与项目历史尝试和已否决方向的区别；不能照搬历史方案。
- implementation_spec 要具体到能直接写代码（只能改项目允许的文件），包括结构、形状、初始化方式，以及如何在所有数据集上用同一实现。
- diagnostics：方案若依赖训练前的诊断，从项目声明的诊断中选择（name 与 args 按项目说明填写），系统会在 prequick 前执行并写入证据；只写在 implementation_spec 里不会被执行。最多 2 条，不需要就留空。
- training_overrides 只在有明确理由时填写，键只能来自项目允许的配置键。
