import { strict as assert } from "node:assert";
import { literatureStatus, type LiteratureSource } from "../src/lib/literatureEvidence";

const source: LiteratureSource = { id: "manual-ui-input", title: "status-label input", url: "", source_id: "", reading_status: "unread", decision: "", reason: "", method_summary: "", limitations: "", complete_pages: [], error: "" };
assert.equal(literatureStatus(source), "未读正文 · 尚未记录筛选结论");
assert.equal(literatureStatus({ ...source, reading_status: "unavailable", decision: "defer" }), "正文获取失败 · 待补充");
assert.equal(literatureStatus({ ...source, reading_status: "method_complete", decision: "reject" }), "方法页阅读完整 · 未采用");
console.log("Literature evidence status checks passed");
