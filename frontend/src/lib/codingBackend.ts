import { boundedFetch } from "./clientPolicy";

export type CodingBackend = "zcode" | "native_llm";
export type CodingBackendStatus = {
  selected: string;
  zcode_available: boolean;
  reason: string;
  applies_to: "new_invocations";
  model_source: "coding_agent";
};

export async function codingBackendSettings(backend?: CodingBackend): Promise<CodingBackendStatus> {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
  const response = await boundedFetch(`${base}/api/config/coding-backend`, backend ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backend }),
  } : {});
  const result: unknown = await response.json();
  if (!response.ok) {
    const detail = result && typeof result === "object" && "detail" in result ? result.detail : null;
    throw new Error(typeof detail === "string" ? detail : "编码引擎设置失败");
  }
  if (!result || typeof result !== "object"
      || !("selected" in result) || typeof result.selected !== "string"
      || !("zcode_available" in result) || typeof result.zcode_available !== "boolean"
      || !("reason" in result) || typeof result.reason !== "string"
      || !("applies_to" in result) || result.applies_to !== "new_invocations"
      || !("model_source" in result) || result.model_source !== "coding_agent") {
    throw new Error("编码引擎设置返回不完整");
  }
  return result as CodingBackendStatus;
}
