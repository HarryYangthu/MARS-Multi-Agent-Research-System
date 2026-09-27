# 通用新建研究向导（G 的先行切片）

主入口 `/runs/new` 现在填写目标、路径、命令、指标和有限预算，再向同一后端预检、冻结和保存。此版本保存的是**待执行计划**，没有发送启动请求；合同任务的执行准入仍由后端阻断。页面已明确说明限制，不能将本次验收称为自动研究完成或工作包 G 全量完成。

## 使用与边界

1. **研究目标**：名称、目标、项目标识和模式。已有项目选择仅复制名称、标识、代码位置，不推断命令、资料或领域指标，也不套用同名项目的隐式规则。
2. **项目路径**：代码、输出、知识与数据引用、基线文件、允许修改与额外保护范围。当前使用结构化路径文本输入，未加入原生文件夹选择。数据留在原处。
3. **命令与指标**：分别声明 check / train / evaluate 的可执行文件、逐行参数、工作目录和入口/配置文件。共用执行环境可以被每条命令显式覆盖。参数不是 shell 字符串；空行忽略，含空格的参数写在同一行，当前不能表达空字符串或含换行参数。指标包含名称、单位、方向、目标与容差，均是要求而非测量值。
4. **预算与预检**：默认值从后端 YAML 配置读取，23 个字段可见或渐进展开。有限值、整数、上下限和预算关系在客户端集中校验；后端继续进行独立校验。费用是上限设置，实际价格/用量可能未知，不是固定收费保证。

只有真实文件预检通过才可冻结；冻结后需另按“保存待执行计划”。任意语义字段修改均废弃旧预检和冻结结果。预检只验证声明文件、路径和可执行文件的可用性，不执行命令、不检查 Python 包依赖、不调用模型、不验证科学结果。SSH/GPU 合同预检仍会明确阻断。

高级区域保留完整冻结合同 JSON 导入；只能校验并保存，不能编辑后继续使用旧指纹。旧项目/PIMC 阶段表单移到 `?mode=legacy`（显式阶段调试参数也保留兼容），该流程仍可能调用旧任务启动 API，页面明确说明它未使用通用合同预算。顶部四个主导航和一个主新建入口保持不变，项目接入对话框不再出现第二个新建按钮。

**跨次复用尚未完成**：未保存草稿只在组件内存中，离页丢失；完整项目命令/指标配置尚没有可编辑的持久配置模板。现有项目名称/路径选择及冻结合同导入，不能冒充完整配置的跨次复用。

## API 与异步行为

| 操作 | 同一后端接口 | 结果含义 |
|---|---|---|
| 默认预算 | `GET /api/research-contracts/defaults` | 配置值 |
| 文件预检 | `POST /api/research-contracts/preflight` | 缺项与文件指纹，不执行 |
| 冻结 | `POST /api/research-contracts/prepare` | 冻结合同，尚未创建任务 |
| 保存 | `POST /api/research-contracts/runs` | `created` / `research_started:false` 及执行阻断 |

所有请求使用 `configs/frontend.yaml` 的等待上限，无自动 mutation 重试。活动请求由 AbortController 取消等待，组件卸载或字段版本变化后不回写旧响应。按钮使用同步 ref 防止同一组件的重复点击。

保存请求发出前，在 sessionStorage 中记录不含路径或用户正文的计划指纹。只有有效成功回执或明确的 4xx 拒绝（不含 408）才清除此标记；超时、断线、5xx 或坏回执保留“结果未知”，页面禁用再次保存并链接同一后端的任务列表。没有“清锁后重试”的入口。

**该标记不是后端幂等保证**：它只在同一浏览器标签页生命周期内生效；关闭标签页、禁用存储、另一标签页或另一个客户端不受它保护。持久请求身份、按请求核对和安全恢复还需后端实现，当前不得声称完成了保存恢复。客户端 abort 不能撤销服务端已受理的保存。

## 实际验收

2026-09-28 使用真实 Next 生产构建、现有隔离本地 FastAPI 服务和专用浏览器会话。临时项目包含真实 Python 源码、基线和知识文件；入口脚本一旦被执行会写哨兵后失败，验收后哨兵不存在。未使用模型、工具或服务成功替身。

| 场景 | 实際结果 |
|---|---|
| 空表单 | 汇总 17 项缺项，不发预检请求；Tab + Enter 从摘要聚焦研究名称 |
| 真正缺失的知识文件 | 后端报告缺项；最终构建以中文显示，并聚焦对应知识输入框 |
| 重复错误阈值填写 3 | 客户端集中拒绝；恢复为 2 后可预检 |
| 已冻结后修改请求预算 60 → 59 | 旧冻结消失，冻结按钮禁用，重新预检/冻结后保存的合同值为 59 |
| 浏览器断网预检 | 显示连接失败；恢复网络后只有显式点击才重新预检 |
| 暂停本次持有的 Next 进程 | 真实 prepare 请求达到配置的 15 秒等待上限，显示超时；恢复进程后可手动重新检查 |
| 保存超时 | 客户端显示结果未知；恢复进程后后端真实保存了任务；刷新仍锁定，未自动重发 |
| 保存等待时导航到任务列表 | 浏览器离页，保留待核对标记；后端后来保存，返回向导仍禁止重发，没有后续 start 请求 |
| 最终源码正常保存 | 界面明确“已保存 · 尚未启动”；后端 `created`、五阶段 pending、只读、无可执行动作 |
| 1280×720 / 1440×900 | 实际截图已查看，无横向溢出；步骤、按钮与错误项可经滚动/键盘到达，焦点可见 |

正常保存两次及两个独立网络故障验收请求共生成四条待执行测试记录；没有自动重复保存。每条均复核真实合同、API 状态和文件，未产生模型/工具/实验回执。脱敏源码哈希、构建标识、截图哈希与验收记录见 [research-wizard-ui-review.json](evidence/research-wizard-ui-review.json)。原始截图和网络记录保存在本机临时验收目录，未打包进发行版。

验证命令（在 `frontend/` 内）：

```sh
./node_modules/.bin/tsc --noEmit --incremental false
./node_modules/.bin/eslint src/components/NewResearchWizard.tsx src/components/FrozenResearchImport.tsx src/components/LegacyResearchForm.tsx src/components/FolderProjectDialog.tsx src/app/runs/new/page.tsx src/lib/researchWizard.ts src/lib/researchContracts.ts src/lib/researchSubmission.ts
./node_modules/.bin/tsc --outDir .tmp/research-wizard --rootDir . --module commonjs --target ES2022 --moduleResolution node --skipLibCheck --noEmit false scripts/research-wizard-smoke.ts src/lib/researchWizard.ts src/lib/researchContracts.ts src/lib/clientPolicy.ts
node .tmp/research-wizard/scripts/research-wizard-smoke.js
BACKEND_URL=http://127.0.0.1:8012 npm run build
```

全部通过。构建保留其他未修改页面已有的 lint warning；没有新增依赖。纯表单检查读取真实 YAML，覆盖有限预算、字段缺失、关系校验、路径/命令构造、重复指标与非有限目标，不调用后端或模型。

初次构建未指定 API rewrite，已在实际 UI 验收前重构建；初次缺失文件文案为英文，已本地化并在最终构建复验。审查发现的重复错误阈值缺口及结果未知手动清锁已修复，不用较早截图替代最终源码验收。

仍待独立验收：五名非开发目标用户的接入测试、高分屏矩阵、磁盘不足与 SSH 断连体验、完整项目配置复用、持久保存核对/幂等恢复，以及准入解除后的真实研究/停止/恢复闭环。
