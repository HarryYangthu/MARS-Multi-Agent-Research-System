import assert from "node:assert/strict";
import { ApiError, reviewResultUncertain } from "../src/lib/apiError";

for (const status of [400, 403, 404, 409, 422]) {
  assert.equal(reviewResultUncertain(new ApiError("Known refusal", status), true), false);
}
assert.equal(reviewResultUncertain(new Error("Document changed before submission"), false), false);
assert.equal(reviewResultUncertain(new TypeError("Network lost before submission"), false), false);
for (const error of [new TypeError("Network lost"), new SyntaxError("Invalid response after submission"), new Error("Unknown submission failure"), new DOMException("Timeout", "TimeoutError"), new ApiError("Server error", 500), new ApiError("Gateway error", 502)]) {
  assert.equal(reviewResultUncertain(error, true), true);
}
console.log("Review refusal and unknown submission outcome checks passed");
