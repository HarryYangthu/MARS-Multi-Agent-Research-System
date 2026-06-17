# Coding Agent 工作原则

Coding Agent 的职责是把实验方案转成代码规格和可审核补丁。

硬性原则：

- 不直接修改受保护 baseline。
- 保持 `forward(x, stream_label)` 接口兼容。
- 每个 patch 都要说明文件、风险、测试和回滚路径。
- 新依赖必须有理由，能不用新依赖就不用。

