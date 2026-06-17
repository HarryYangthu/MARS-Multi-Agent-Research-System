"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export type Lang = "zh" | "en";

type Dict = Record<string, string>;

const ZH: Dict = {
  // top bar
  "app.title": "MARS · 多 Agent 研究系统",
  "app.version": "v0.1.0",
  "stat.agents": "Agent 数",
  "stat.running": "工作中",
  "stat.failed": "异常",
  "stat.artifacts": "产物",
  "stat.kb": "KB 条目",
  "lang.toggle": "EN",
  "settings": "设置",
  "topbar.entries": "入口卡片",
  "topbar.lab": "实验台主页",
  "entries.title": "独立 Agent 入口",
  "entries.subtitle": "选择 Pipeline 全链路或某个独立 Agent 启动新任务",
  "entries.back": "← 返回实验台",
  "entries.start": "启动",
  "entries.card.pipeline.title": "Pipeline · 全链路",
  "entries.card.pipeline.blurb": "构想 → 实验 → 编码 → 执行 → 写作,完整 5 节点人机审核链路",
  "entries.card.idea.blurb": "调研问题 → 假设(默认开启多模型辩论)",
  "entries.card.experiment.blurb": "假设 → 消融矩阵 + Baseline 自动复用决策",
  "entries.card.coding.blurb": "实验方案 → patch + tests + Gate 5 baseline 兼容性检查",
  "entries.card.execution.blurb": "patch + batch_config → 16 组仿真 + 实时曲线(最多 16 并发)",
  "entries.card.writing.blurb": "全链路产物 → 研究报告(默认开启审稿式辩论)",
  "layer.empty.cta": "+ 启动独立运行",
  "layer.empty.hint": "该层暂无活跃任务,点击启动",
  "debate.title": "多模型辩论",
  "debate.show": "▾ 展开辩论转录",
  "debate.hide": "▴ 收起辩论转录",
  "debate.empty": "本 Agent 没有 debate(配置中 debate.enabled=false 或本次 run 未启用)",
  "debate.modeLabel": "模式",
  "stat.waiting": "待审核",
  "hitl.banner.title": "✋ 等待您审核",
  "hitl.banner.body": "本节点已经完成草稿，Schema 校验已生效。请检查/编辑后点击批准，只有批准之后下一节点才会启动。驳回会标记本节点失败并停止链路。",
  "hitl.cta.openWaiting": "打开待审核 run",
  "newrun.template.show": "📋 当前入口将使用 Schema 模板,可直接改参数",
  "newrun.template.note": "提交时会用 Schema 校验;不通过的字段会指出位置。",
  "newrun.mode.research": "研究问题模式(适用于 Pipeline / Idea Agent)",
  "newrun.mode.template": "模板填写模式(独立 Agent 入口)",
  "newrun.field.research": "研究问题",
  "newrun.field.markdown": "Schema 模板(YAML frontmatter + body)",
  "newrun.submit.failed": "Schema 校验失败:",
  "execution.live.title": "🔴 实时仿真曲线",
  "execution.live.empty": "等待第一条曲线数据…",
  "execution.live.gotoMulti": "多实验视图 →",
  "execution.live.focus": "当前实时曲线",
  "execution.live.folded": "其余实验已在主视图折叠",
  "execution.live.foldedHint": "完整多实验曲线请进入多实验视图查看。",
  "execution.live.otherRuns": "其他实验",
  "execution.live.updated": "已更新",
  "run.editor.save": "保存编辑（新版本）",
  "run.empty.runningWithDebate": "⚙️ Agent 正在运行，多模型辩论进行中。下方转录约每 1.5 秒刷新一次。",
  "run.empty.running": "⚙️ Agent 正在运行。首次模型调用可能需要 20–60 秒；第一轮完成后，辩论转录会在这里流式显示。",
  "run.empty.noArtifact": "该阶段还没有产物。请等待 Agent 起草，或从首页启动新的 run。",
  "artifact.valid": "✓ Schema {schema} 有效 · 版本 {version}",
  "artifact.invalid": "✗ Schema {schema} 无效:",
  "artifact.body": "产物正文",
  "artifact.metadata": "Schema 元数据",
  "artifact.editorHint": "当前默认展示正文内容；Schema frontmatter 保留在下方折叠区，保存时会继续用于校验。",
  "artifact.frontmatterMissing": "未找到 frontmatter 分隔符 ---，将按正文保存。",
  "patch.status.approved": "已批准",
  "patch.status.pending": "待处理",
  "trace.title": "执行追踪",
  // tabs
  "tab.lab_pipeline": "实验台·全链路",
  "tab.lab_standalone": "实验台·独立",
  "tab.paper_repro": "论文复现",
  // sidebar
  "sidebar.projects": "项目管理",
  "sidebar.running": "运行中",
  "chat.title": "主控对话",
  "chat.autoToggle": "切换介入模式(半自动/全自动)",
  "chat.auto": "全自动",
  "chat.semi": "半自动",
  "chat.new": "新会话",
  "chat.hello": "你好,我是 MARS Commander Agent。告诉我你的研究目标 —— 我会理解意图、智能选择入口(已有想法就跳过 Idea)、调度 5 个 Agent,并在执行不达预期时自动追责、拉回重跑。",
  "chat.thinking": "思考中…",
  "chat.send": "发送",
  "chat.placeholder": "告诉我你的研究目标,Enter 发送(Shift+Enter 换行)",
  "sidebar.input.research": "研究问题",
  "sidebar.input.research_placeholder": "示例:如何在 8L 配置下进一步降低 ATK-MoE 的计算资源,同时保持 RES 性能?",
  "sidebar.input.tags": "研究方向(分号隔开)",
  "sidebar.input.tags_placeholder": "默认 CV,如:CV; VLM; World Model",
  "sidebar.input.config": "配置信息",
  "sidebar.input.project": "项目名 (slug)",
  "sidebar.input.project_placeholder": "moe-pimc",
  "sidebar.input.entrypoint": "入口",
  "sidebar.submit": "提交",
  "sidebar.no_runs": "暂无运行任务",
  // pipeline layers — Agent names are the source of truth (English),
  // tier index is the only Chinese label so we don't put inaccurate
  // descriptive translations on the Agents themselves.
  "layer.1": "第一层",
  "layer.2": "第二层",
  "layer.3": "第三层",
  "layer.4": "第四层",
  "layer.5": "第五层",
  "layer.idle": "空闲",
  "layer.active": "活跃",
  "layer.agents_count": "Agent",
  // agent cards
  "agent.idea": "Idea Agent",
  "agent.experiment": "Experiment Agent",
  "agent.coding": "Coding Agent",
  "agent.execution": "Execution Agent",
  "agent.writing": "Writing Agent",
  // states
  "state.pending": "待处理",
  "state.running": "工作中",
  "state.waiting_review": "等待审核",
  "state.approved": "已批准",
  "state.done": "已完成",
  "state.failed": "失败",
  "state.skipped": "已跳过",
  "state.unknown": "未知",
  // event log
  "events.title": "事件日志",
  "events.empty": "暂无事件",
  "events.filter.all": "全部",
  "events.filter.l1": "调研与创意",
  "events.filter.l2": "实验设计",
  "events.filter.l3": "代码与资源",
  "events.filter.l4": "执行与修正",
  "events.filter.l5": "论文写作",
  // KB
  "kb.title": "共享数据仓库",
  "kb.literature": "Idea 仓库",
  "kb.literature.subtitle": "文献卡片、知识综合、研究假设",
  "kb.methodology": "方法库",
  "kb.methodology.subtitle": "方法论、实验模板、消融模式",
  "kb.code_assets": "代码资产库",
  "kb.code_assets.subtitle": "可复用模块、算子、训练脚手架",
  "kb.run_archive": "实验运行档案",
  "kb.run_archive.subtitle": "历史 run、Baseline Fingerprint",
  "kb.empty": "暂无条目",
  "kb.search": "搜索",
  "kb.search.placeholder": "搜索记忆",
  "kb.search.allZones": "全部主库",
  "kb.search.allTypes": "全部类型",
  "kb.type.semantic": "语义",
  "kb.type.episodic": "事件",
  "kb.type.procedural": "流程",
  "kb.includeMock": "mock",
  "kb.includeSuperseded": "旧版本",
  "kb.quarantine": "隔离区",
  "kb.main": "主库",
  // human feedback
  "feedback.title": "人工反馈",
  "feedback.placeholder": "输入对当前任务的批注,回车发送",
  // run actions
  "run.pause": "暂停",
  "run.resume": "重启",
  "run.delete": "删除",
  "run.detail": "查看详情",
  "run.approve": "批准",
  "run.reject": "驳回",
  // misc
  "common.refresh": "刷新",
  "common.loading": "加载中…",
  "common.error": "出错:",
  "common.created_at": "创建时间",
  "common.updated_at": "更新时间",
  "common.task": "任务",
  "common.project": "项目",
  "common.entrypoint": "入口",
  "common.run_id": "Run ID",
  "common.search": "搜索",
};

const EN: Dict = {
  "app.title": "MARS · Multi-Agent Research System",
  "app.version": "v0.1.0",
  "stat.agents": "Agents",
  "stat.running": "Working",
  "stat.failed": "Failed",
  "stat.artifacts": "Artifacts",
  "stat.kb": "KB items",
  "lang.toggle": "中",
  "settings": "Settings",
  "topbar.entries": "Entries",
  "topbar.lab": "Lab",
  "entries.title": "Standalone Agent entries",
  "entries.subtitle": "Pick the full Pipeline, or a single Agent to start a new task",
  "entries.back": "← Back to Lab",
  "entries.start": "Start",
  "entries.card.pipeline.title": "Pipeline · full chain",
  "entries.card.pipeline.blurb": "Idea → Experiment → Coding → Execution → Writing — full 5-node HITL chain",
  "entries.card.idea.blurb": "Research question → hypothesis (multi-model debate default-on)",
  "entries.card.experiment.blurb": "Hypothesis → ablation matrix + auto baseline reuse",
  "entries.card.coding.blurb": "Plan → patch + tests + Gate 5 baseline-compatibility check",
  "entries.card.execution.blurb": "Patch + batch config → 16 simulations + live curves (≤16 concurrent)",
  "entries.card.writing.blurb": "Full chain artifacts → research report (reviewer debate default-on)",
  "layer.empty.cta": "+ Start standalone run",
  "layer.empty.hint": "No active task on this tier — click to launch",
  "debate.title": "Multi-model Debate",
  "debate.show": "▾ Show debate transcript",
  "debate.hide": "▴ Hide debate transcript",
  "debate.empty": "No debate for this Agent (debate.enabled=false in config, or not active this run)",
  "debate.modeLabel": "mode",
  "stat.waiting": "Waiting",
  "hitl.banner.title": "✋ Waiting for your review",
  "hitl.banner.body": "This node has produced its draft and the schema check has run. Please review / edit, then click Approve — only after Approve will the next node start. Reject marks this node as failed and halts the chain.",
  "hitl.cta.openWaiting": "Open waiting run",
  "newrun.template.show": "📋 This entry uses the schema template — edit parameters in place",
  "newrun.template.note": "On submit the markdown is schema-validated; failing fields are pinpointed.",
  "newrun.mode.research": "Research-question mode (Pipeline / Idea agent)",
  "newrun.mode.template": "Template mode (standalone agent entries)",
  "newrun.field.research": "Research question",
  "newrun.field.markdown": "Schema template (YAML frontmatter + body)",
  "newrun.submit.failed": "Schema validation failed:",
  "execution.live.title": "🔴 Live simulation curves",
  "execution.live.empty": "Waiting for the first curve datapoint…",
  "execution.live.gotoMulti": "Multi view →",
  "execution.live.focus": "Focused live curve",
  "execution.live.folded": "Other experiments are folded in this view",
  "execution.live.foldedHint": "Open Multi view for the full experiment grid.",
  "execution.live.otherRuns": "Other experiments",
  "execution.live.updated": "updated",
  "run.editor.save": "Save edit (new version)",
  "run.empty.runningWithDebate": "⚙️ Agent is running — multi-model debate in progress. Transcript below updates every ~1.5s.",
  "run.empty.running": "⚙️ Agent is running. The first model call may take 20–60s. The debate transcript will stream in here as soon as the first turn completes.",
  "run.empty.noArtifact": "No artifact for this stage yet. Wait for the agent to draft, or start a new run from the Dashboard.",
  "artifact.valid": "✓ Schema {schema} valid · version {version}",
  "artifact.invalid": "✗ Schema {schema} invalid:",
  "artifact.body": "Artifact body",
  "artifact.metadata": "Schema metadata",
  "artifact.editorHint": "This editor shows the markdown body by default. Frontmatter is kept below and still used for validation on save.",
  "artifact.frontmatterMissing": "Frontmatter delimiters --- not found; saving as body only.",
  "patch.status.approved": "approved",
  "patch.status.pending": "pending",
  "trace.title": "Trace",
  "tab.lab_pipeline": "Lab · Pipeline",
  "tab.lab_standalone": "Lab · Standalone",
  "tab.paper_repro": "Paper Repro",
  "sidebar.projects": "Projects",
  "sidebar.running": "running",
  "chat.title": "Commander",
  "chat.autoToggle": "Toggle intervention mode (semi/auto)",
  "chat.auto": "auto",
  "chat.semi": "semi",
  "chat.new": "new chat",
  "chat.hello": "Hi, I'm the MARS Commander. Tell me your research goal — I'll read your intent, pick the right entry point (skip Idea if you already have one), orchestrate the 5 agents, and auto-diagnose + pull back + rerun when results miss the target.",
  "chat.thinking": "thinking…",
  "chat.send": "Send",
  "chat.placeholder": "Tell me your research goal. Enter to send (Shift+Enter for newline)",
  "sidebar.input.research": "Research question",
  "sidebar.input.research_placeholder": "例如：如何在 8L 配置下进一步降低 ATK-MoE 的计算资源，同时保持 RES 性能？",
  "sidebar.input.tags": "Topics (semicolon separated)",
  "sidebar.input.tags_placeholder": "e.g. CV; VLM; World Model",
  "sidebar.input.config": "Config",
  "sidebar.input.project": "Project (slug)",
  "sidebar.input.project_placeholder": "moe-pimc",
  "sidebar.input.entrypoint": "Entry point",
  "sidebar.submit": "Submit",
  "sidebar.no_runs": "No runs yet",
  "layer.1": "Tier 1",
  "layer.2": "Tier 2",
  "layer.3": "Tier 3",
  "layer.4": "Tier 4",
  "layer.5": "Tier 5",
  "layer.idle": "idle",
  "layer.active": "active",
  "layer.agents_count": "agents",
  "agent.idea": "Idea Agent",
  "agent.experiment": "Experiment Agent",
  "agent.coding": "Coding Agent",
  "agent.execution": "Execution Agent",
  "agent.writing": "Writing Agent",
  "state.pending": "pending",
  "state.running": "running",
  "state.waiting_review": "waiting review",
  "state.approved": "approved",
  "state.done": "done",
  "state.failed": "failed",
  "state.skipped": "skipped",
  "state.unknown": "unknown",
  "events.title": "Event log",
  "events.empty": "No events yet",
  "events.filter.all": "All",
  "events.filter.l1": "Tier 1",
  "events.filter.l2": "Tier 2",
  "events.filter.l3": "Tier 3",
  "events.filter.l4": "Tier 4",
  "events.filter.l5": "Tier 5",
  "kb.title": "Shared knowledge",
  "kb.literature": "Literature",
  "kb.literature.subtitle": "papers, snippets, hypotheses",
  "kb.methodology": "Methodology",
  "kb.methodology.subtitle": "methods, templates, ablation patterns",
  "kb.code_assets": "Code assets",
  "kb.code_assets.subtitle": "reusable modules, operators, scaffolds",
  "kb.run_archive": "Run archive",
  "kb.run_archive.subtitle": "historical runs, baseline fingerprints",
  "kb.empty": "Empty",
  "kb.search": "Search",
  "kb.search.placeholder": "Search memory",
  "kb.search.allZones": "All main zones",
  "kb.search.allTypes": "All types",
  "kb.type.semantic": "Semantic",
  "kb.type.episodic": "Episodic",
  "kb.type.procedural": "Procedural",
  "kb.includeMock": "mock",
  "kb.includeSuperseded": "old versions",
  "kb.quarantine": "Quarantine",
  "kb.main": "Main",
  "feedback.title": "Human feedback",
  "feedback.placeholder": "Comment on the current task. Press Enter to send.",
  "run.pause": "Pause",
  "run.resume": "Resume",
  "run.delete": "Delete",
  "run.detail": "Open",
  "run.approve": "Approve",
  "run.reject": "Reject",
  "common.refresh": "Refresh",
  "common.loading": "Loading…",
  "common.error": "Error: ",
  "common.created_at": "Created",
  "common.updated_at": "Updated",
  "common.task": "Task",
  "common.project": "Project",
  "common.entrypoint": "Entry point",
  "common.run_id": "Run ID",
  "common.search": "Search",
};

const DICTS: Record<Lang, Dict> = { zh: ZH, en: EN };

type I18nCtx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
  toggle: () => void;
};

const Ctx = createContext<I18nCtx | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [lang, setLangState] = useState<Lang>("zh");

  // Hydrate from localStorage after mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem("mars.lang") as Lang | null;
    if (saved === "zh" || saved === "en") setLangState(saved);
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      window.localStorage.setItem("mars.lang", l);
    } catch {
      /* ignore */
    }
  }, []);

  const t = useCallback(
    (key: string) => {
      const d = DICTS[lang];
      return d[key] ?? key;
    },
    [lang],
  );

  const toggle = useCallback(() => {
    setLang(lang === "zh" ? "en" : "zh");
  }, [lang, setLang]);

  const value = useMemo<I18nCtx>(
    () => ({ lang, setLang, t, toggle }),
    [lang, setLang, t, toggle],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nCtx {
  const v = useContext(Ctx);
  if (v === null) {
    // Fallback when used outside provider — return a default zh translator.
    return {
      lang: "zh",
      setLang: () => {},
      toggle: () => {},
      t: (key) => DICTS.zh[key] ?? key,
    };
  }
  return v;
}

export function tStateLabel(state: string, t: (k: string) => string): string {
  return t(`state.${state}`) || state;
}
