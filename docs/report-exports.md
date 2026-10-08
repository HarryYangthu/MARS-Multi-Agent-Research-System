# 报告导出与报告 skill

在主研究对话选择「报告」，或进入研究结果页，使用「报告文件」。报告审核批准后，默认保存 Markdown 正文、关键图片和来源校验记录，不自动生成 Office 文件。

需要其他格式时，分别点击「生成 Excel」「生成 Word」「生成 PPT」，只生成所选格式。已生成的文件提供下载与重新生成入口；生成另一种格式会保留同一份报告和输入记录下已生成的文件。整个保存与转换过程不要求重新调用模型或重新执行实验。

Markdown 和图片资料包包含 `research_report.md` 与 `images/`，解压后可直接打开 Markdown 查看图片。也可以单独下载 Markdown 或图片；单独下载时需按正文中的相对路径将图片放在 `images/` 下。历史报告第一次使用时，可以点击「保存 Markdown 与图片」归档已有内容。

## 三种文件

- Excel：真实实验指标、完整报告正文、来源路径与校验值、已有训练记录；指标保留数值类型，曲线使用原生图表。显示时普通指标保留两位小数，loss 和学习率保留六位；底层数值不按显示精度截断。
- Word：已批准报告的完整正文、标题、表格和已记录的实验图片，可直接编辑。
- PPT：已批准报告按章节分页，正文与实验指标表可以编辑，已保存的实验图片保持比例。它是报告内容的演示文稿导出，不会新增研究结论。

缺少实验记录时明确标注未记录，不补造指标。导出不会把未通过审核的报告作为最终报告。报告批准内容改变后，页面提示重新生成；旧文件仍保留在独立的生成目录。

## 接入团队的写作 skill

展开「报告 Skill」，导入 UTF-8 的 `SKILL.md`，勾选并保存。示例见 [evidence-report/SKILL.md](report-skills/evidence-report/SKILL.md)。

```markdown
---
name: team-report
description: 团队研究报告的写作与证据规范
---
分别说明研究目标、实验条件、结果、局限与复现方法。
区分事实与假设；结论必须引用实际记录，不得补造数值。
```

文件限 100 KB，顶部须有 `name`、`description`，后面填写指令正文。可用 `required_tools` 声明所需工具；缺少已授权工具时，页面明确显示不可用。导入本身不会授予工具权限。

保存的选择作用于当前项目后续新启动的报告写作。写作开始时固定 skill 版本与内容校验值，并进入 Writing Agent 上下文；恢复同一任务时继续使用固定版本。已写完的报告不会因修改 skill 自动重写，已有报告导出始终转换已批准的原文。

当前接入范围是指令型 `SKILL.md` 和兼容 `report.v1` 的注册 skill，不会上传或自动执行 skill 附带的脚本、安装器和附件。更换内容会产生新版本，需显式选中保存。团队仍需审核生成的研究结论。

## 配置与接口

`configs/reporting.yaml` 控制各格式开关与文件名，默认不在批准时生成 Office 文件。标准安装通过 `pyproject.toml` 安装 Office 生成依赖，不要求安装桌面 Office，也不依赖 Codex 私有运行环境。

导入指令保存到 `configs/skills/imported/<内容校验值>/SKILL.md` 并注册到 `configs/skills.yaml`；项目选择保存在本地 `configs/report_skill_selection.yaml`。运行固定的选择与清单保存在各任务 `input/report_skills/`。这些是本地用户配置与运行记录，不应当随产品源代码提交。

产品接口均经过 API → bridge：

- `GET /api/reports/{run_id}`：当前导出清单、批准状态。
- `POST /api/reports/{run_id}/regenerate`：无 body 或 `{"formats":[]}` 只保存 Markdown 与图片；`{"formats":["excel"]}`、`{"formats":["word"]}`、`{"formats":["powerpoint"]}` 分别转换对应格式。CLI/产品内调用 `generate_report_bundle(run, formats=("word",))` 同样只生成 Word，默认不传 formats 只保存基础报告资料。
- `GET /api/reports/{run_id}/files/{filename}?manifest=writing/report_bundle.vN.md`：下载固定清单中的文件，检查文件校验值。
- `GET /api/reports/{run_id}/skills`：兼容报告的 skill 与项目选择。
- `POST /api/reports/{run_id}/skills/import`：`{"content": "SKILL.md 文本"}`。
- `PUT /api/reports/{run_id}/skills`：`{"ids": ["skill_id"]}`。

基础文件包包括 Markdown 快照、关键图片、资料压缩包和数据记录。Office 文件只在明确请求后生成，记录来源与文件 SHA-256。下载只允许固定清单内已完成且校验值匹配的文件。生成失败会显示失败原因，不把未成功的文件作为可下载成果。来源记录改变时，旧格式文件仍保留在历史目录，但不作为当前报告结果复用。
