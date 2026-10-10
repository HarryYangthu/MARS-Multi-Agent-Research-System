# 研究文档折叠验证

## 行为

- 主聊天中的研究提案、实验方案、代码方案、实验记录、研究报告默认收起。
- 卡片标题、阶段状态和独立审核入口保持可见；正文、附加说明、研究依据和详情链接随卡片展开或收起。
- 审核弹窗默认展开，也支持收起；文档校验与审核操作不依赖正文是否可见。
- 按钮使用 `aria-expanded` 和独立的 `aria-controls`，支持 Enter / Space。
- 文档读取与状态轮询继续运行，不会重置用户选择的展开状态。

## 已完成检查

- 前端 TypeScript 类型检查通过。
- `frontend/scripts/review-prompt-smoke.ts` 的审核状态与文档身份检查通过。
- 使用真实运行中的前后端和 Test5 会话检查五个阶段：默认收起、Enter 展开、Space 收起、正文可见性与控制区域绑定均通过。
- 审核弹窗检查通过：默认展开；真实文档加载并通过校验后，收起正文不会隐藏或禁用批准按钮；关闭弹窗后主聊天仍然收起，审核入口仍可用。
- 页面自动更新后，主聊天卡片仍然保持收起。
- 没有批准或驳回研究产物，也没有启动实验。

浏览器回归函数位于 `frontend/scripts/artifact-collapse-smoke.ts`。将其编译后传入真实浏览器标签，先运行 `verifyArtifactCollapse(tab)`；打开编码审核弹窗，等待真实文档加载完成，再运行 `verifyCollapsedReview(tab)`。这些检查只操作显示状态，不提交审核决定。

本地截图：`runs/artifact-collapse-ui-verification/collapsed.png`。
