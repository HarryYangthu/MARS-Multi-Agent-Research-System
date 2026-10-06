# 确定的执行交接

此文件描述接口，不用于调用模型。Execution Agent 直接读取已批准 `experiment_plan` 和 `code_spec`，将矩阵与 `execution_jobs` 中的启动绑定合并，生成 schema 合规的交接清单。

`execution_phase=planned`、`status=interrupted` 表示未执行；`metrics` 只记录组数。真实测量、耗时、状态和日志来自后续作业收据。主聊天的配置确认是启动前的人工核对，不再审核另一份模型生成的实验方案。

缺少具体入口、配置、种子或预算单位，或交付改写批准参数时阻断。执行侧不得生成新方案、替换配置、扩大扫描或声称已有研究结果。
