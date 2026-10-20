import assert from "node:assert/strict";
import { interfaceText } from "../src/lib/interfaceText";
import { formatActivityElapsed } from "../src/lib/researchActivity";

assert.equal(interfaceText("en", "研究对话"), "Research chat");
assert.equal(interfaceText("zh", "研究对话"), "研究对话");
assert.equal(interfaceText("en", "等待核对仿真配置"), "Awaiting simulation settings");
// Unknown labels and prototype names must remain strings rather than resolve
// to properties inherited from Object.prototype.
for (const text of ["自定义项目 / 中文数据.pth", "__proto__", "constructor", "toString", ""]) {
  assert.equal(interfaceText("en", text), text);
}
assert.equal(formatActivityElapsed(0, "en"), "0s");
assert.equal(formatActivityElapsed(3661, "en"), "1h 1m 1s");
assert.equal(formatActivityElapsed(61), "1 分 1 秒");
console.log("Language label, fallback, and duration checks passed");
