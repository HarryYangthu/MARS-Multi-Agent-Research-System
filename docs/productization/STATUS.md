# 产品化实施状态

更新日期：2026-09-28。执行依据：[PRODUCTIZATION_PLAN.md](../PRODUCTIZATION_PLAN.md)。

发布结论：**未达到发布要求**。此分支交付经过验证的工程增量；安装包、完整研究闭环和发布矩阵尚未通过。

## 工作现场与交付

- 实施基线 `4d703f7`；原桌面 `main` 的六项已跟踪修改及未跟踪资料保持原状。私有补丁快照存于本机隔离工作树旁。
- 分支 `codex/productization-foundation` 使用独立托管工作树。已同步 [草稿 PR #25](https://github.com/HarryYangthu/MARS-Multi-Agent-Research-System/pull/25)。
- 原 `.env`、研究数据和运行历史未复制进源码或发行包；真实 GLM 检查仅通过本机进程环境注入凭据。

## 工作包

| 包 | 状态 | 已完成增量与剩余门槛 |
|---|---|---|
| A | 进行中 | 基线冻结、精确发行清单、macOS 生产页面桌面壳和进程生命周期已有证据；干净安装、完整清理和升级仍未完成 |
| B | 进行中 | 通用项目/任务合同、有限预算、共享 API/CLI 预检与冻结；现有 UI/CLI 执行循环尚未合并，领域执行参数尚待 adapter 化 |
| C | 进行中 | 全部默认角色及辩论参与者改用 GLM-5.3；原生只读文件工具闭环实测；完整能力库及逐项认证仍待完成 |
| D | 进行中 | SDK 自动重试计入全局模型请求上限，未知请求保守预留；状态/预算统一事务、分项 token 与活动时间、完整取消恢复仍待完成 |
| E | 未开始 | 等待统一执行服务与 D；现有流程不能代替此次完整研究验收 |
| F | 未开始 | SSH 密码、远端环境和两种代码接入模式待实施/实测 |
| G | 未开始 | 现有页面已可在桌面壳打开；统一用户旅程、异常状态和用户测试仍未实施 |
| H | 未开始 | 修正旧 CLI 报告中的请求/SDK 尝试/未知额度计数；结果中心与离线复现包仍未实施 |
| I | 未开始 | 内置运行时、安装、升级回滚、平台凭据存储、签名和最终清理尚未完成 |
| J | 未开始 | 未冻结发布候选，也未执行同一构建的 20 次发布矩阵 |

## 证据索引

- [VERIFICATION.md](VERIFICATION.md)：本批完整回归、真实 GLM 型号/工具回执及明确验收边界。

- [BASELINE.md](BASELINE.md)：初始失败、环境边界、修复及回归。Python 3.13.9 本机检查不替代 CI 的 Python 3.11 或 Windows 实机。
- [CLEANUP_INVENTORY.md](CLEANUP_INVENTORY.md)：精确清单及动态依赖；已删除未使用个人页面/图片，移除受跟踪的 TypeScript 缓存，清空默认个人执行路径。
- [DESKTOP_DECISION.md](DESKTOP_DECISION.md)：实际 Electron/Python/Next 生产页面验证；[全部烟测尝试](evidence/desktop-smoke-attempts.json)、[安全回归](evidence/desktop-security-checks.json)、[第二实例](evidence/desktop-second-instance.json)。保留失败，不拼接成发布批次。
- [PROJECT_CONTRACT.md](PROJECT_CONTRACT.md)：API/CLI 预检、冻结与保护范围。返回 prepared 明确不代表研究已经启动。

桌面原型仍依赖开发环境中的 Python 和 Next 生产构建；打包状态明确拒绝启动，避免被误认为可安装产品。真实 GLM 检查只证明指定工具与协议链路，费用因缺少核验价格表显示未知；不证明正式 Idea Reflection、论文审查、PIMC 仿真或科学目标达成。

当前发行门禁仍阻断：源码存在需要迁移的领域标记，Git 历史秘密扫描有四条待分类候选；没有生成可发布归档，也没有改写历史或放松扫描规则。

## 外部验收资源

Windows 11 x64 干净实机、Linux GPU/SSH 两种认证环境、发布签名/公证资源、五名目标用户及授权研究输入尚未完成此次核验。对应门槛不豁免，其余开发继续。
