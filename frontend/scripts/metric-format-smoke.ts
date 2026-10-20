import assert from "node:assert/strict";
import { formatMetric } from "../src/lib/metricFormat";

assert.equal(formatMetric(0.00256207), "0.002562");
assert.equal(formatMetric(0.003444187), "0.003444");
assert.equal(formatMetric(0.004923), "0.004923");
assert.equal(formatMetric(0.004639416933059692 - 0.0049231587909162045), "-0.0002837");
assert.equal(formatMetric(1.23456e-7), "1.235e-7");
assert.equal(formatMetric(-1.23456e-7), "-1.235e-7");
assert.equal(formatMetric(0), "0");
assert.equal(formatMetric(-0), "0");
assert.equal(formatMetric(1382), "1382");
assert.equal(formatMetric(18.58), "18.58");
assert.equal(formatMetric(18.586), "18.59");
assert.equal(formatMetric(0.01), "0.01");
assert.equal(formatMetric(-0.01), "-0.01");
assert.equal(formatMetric(0.0099999), "0.01000");
for (const missing of [null, undefined, NaN, Infinity, -Infinity]) {
  assert.equal(formatMetric(missing), "—");
}
console.log("Small metrics and deltas retain significant digits; ordinary metrics and missing values remain compatible.");
