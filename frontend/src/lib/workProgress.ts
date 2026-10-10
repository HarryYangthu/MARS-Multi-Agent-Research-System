import type { Activity } from "./researchActivity";

const TOOLS: Record<string, string> = {
  "code.repo_read_lines": "读取相关代码片段", "code.repo_list": "查看项目代码目录", "code.repo_search": "搜索相关代码与配置",
  "code.repo_reader": "读取项目代码", "context.read_material": "阅读研究资料", "context.readback": "查阅已保存的材料",
  "search.fetch_sources": "下载论文和资料", "search.web": "检索相关资料", "search.search_papers": "检索相关论文",
  "create_and_start_run": "启动研究流程", "run.recover": "恢复研究任务", "artifact.write": "保存阶段成果",
  "code.write_file": "写入代码", "code.apply_patch": "修改代码", "code.run_tests": "运行代码检查",
  "execution.run": "执行实验", "research.delegate": "开展论文调研",
  "knowledge.baseline_match": "核对基线约束", "knowledge.experiment_memory": "检索历史实验",
  "code.lint": "检查代码语法", "search.openalex_search": "检索研究文献",
};
export function toolWork(tool: string): string {
  if (TOOLS[tool]) return TOOLS[tool];
  if (/search|arxiv|semantic|crossref/.test(tool)) return "检索研究资料";
  if (/read|inspect|list|tree/.test(tool)) return "查阅文件与资料";
  if (/test|compile|validate/.test(tool)) return "检查代码与配置";
  if (/write|patch|edit/.test(tool)) return "写入代码与成果";
  if (/run|execute/.test(tool)) return "运行任务";
  return `使用工具 ${tool}`;
}
export function workProgress(items: Activity[]): Activity[] {
  const result: Activity[] = [];
  const tools = new Map<string, number>();
  for (const item of items) {
    if (/状态变化|发起模型调用|模型已返回|压缩上下文|产物质量|检查输出格式|验收报告|检查方案格式|任务记录已创建|已记录工作流启动/.test(item.title) || item.startsStage || item.endsStage) continue;
    let next = item;
    if (item.title.startsWith("调用工具 ")) next = { ...item, title: toolWork(item.title.replace("调用工具 ", "")), detail: item.detail === "执行工具调用。" ? "" : item.detail };
    if (next.detail.length > 300) next = { ...next, detail: next.detail.slice(0, 300) + "…（完整记录见 Agent 详情）" };
    if (next.agent === "bridge" && next.title.includes("实验批次")) next = { ...next, agent: "execution" };
    if (next.detail === "query=") next = { ...next, detail: "" };
    if (item.kind === "tool_dispatch" || item.kind === "observation") {
      const tool = item.detail.split(" · ").at(-1) || "";
      if (!tool) continue;
      const key = `${item.agent}:${tool}`;
      next = { ...item, title: `${toolWork(tool)}${item.status === "failed" ? " · 未完成" : item.kind === "observation" ? " · 已完成" : ""}`, detail: "" };
      const pending = tools.get(key);
      if (item.kind === "observation" && pending !== undefined) {
        result[pending] = { ...next, id: result[pending].id, timestamp: result[pending].timestamp, ended_at: item.timestamp };
        tools.delete(key); continue;
      }
      if (item.kind === "tool_dispatch") tools.set(key, result.length);
    }
    // Adjacent duplicated receipts are one visible piece of work; raw events
    // remain accessible through Agent details.
    const previous = result.at(-1);
    const sameRevision = previous && /人工.*(?:返工|修改意见)|按人工意见返工/.test(previous.title) && /人工.*(?:返工|修改意见)|按人工意见返工/.test(next.title);
    if (previous?.agent === next.agent && (previous.title === next.title || sameRevision) && previous.detail === next.detail) result[result.length - 1] = { ...next, id: previous.id, timestamp: previous.timestamp };
    else result.push(next);
  }
  return result;
}
