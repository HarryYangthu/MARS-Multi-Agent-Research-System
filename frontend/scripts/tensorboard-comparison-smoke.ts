import assert from "node:assert/strict";
import { canOverlayImages, comparisonUrl, exactRunFilter, runPathFilter, type ComparisonSource } from "../src/lib/tensorboardComparison";

const source = (id: string): ComparisonSource => ({ id, name: id, label: id, is_baseline: id === "baseline",
  native: false, scalar_run: `logs/${id}.v1`, histogram_run: `weights/${id}`, image_run: "figures/attempt1",
  image_tags: [`figures/${id}/spectrum.png`], comparison_tags: ["figures/comparison_spectrum.png"], attempt: "attempt1",
  training_steps: 3800, validation_points: 101 });
const baseline = source("baseline");
const candidate = source("candidate+1");
const pair = [baseline, candidate];
const filter = new RegExp(exactRunFilter(pair.map((item) => item.scalar_run)));
assert(filter.test(baseline.scalar_run));
assert(filter.test(candidate.scalar_run));
assert(!filter.test("logs/candidate1.v1"));
assert(!filter.test(baseline.scalar_run + "/another-attempt"));
const scalarUrl = comparisonUrl("/api/tensorboard/view/key/", "scalars", pair, true);
const scalarSettings = new URLSearchParams(scalarUrl.split("&").slice(1).join("&"));
assert.equal(scalarSettings.get("regexInput"), runPathFilter(pair.map((item) => item.scalar_run)));
const portableFilter = new RegExp(scalarSettings.get("regexInput") || "(?!)");
assert(portableFilter.test(baseline.scalar_run));
assert(portableFilter.test(candidate.scalar_run.replaceAll("/", "\\")));
assert(!portableFilter.test(candidate.scalar_run.replace("+", "")));
assert(!portableFilter.test(candidate.scalar_run + "/other-attempt"));
assert.equal(scalarSettings.get("_smoothingWeight"), "0");
assert(new RegExp(scalarSettings.get("tagFilter") || "").test("validation/RES_db"));
assert(canOverlayImages(pair, pair));
assert(!canOverlayImages(pair, [...pair, source("another-candidate")]));
assert(!canOverlayImages([baseline, { ...candidate, attempt: "different" }], pair));
const imageUrl = comparisonUrl("/api/tensorboard/view/key/", "images", [baseline], false);
const imageSettings = new URLSearchParams(imageUrl.split("&").slice(1).join("&"));
assert(new RegExp(imageSettings.get("tagFilter") || "").test(baseline.image_tags[0]));
assert(!new RegExp(imageSettings.get("tagFilter") || "").test(candidate.image_tags[0]));
console.log("TensorBoard source selection, overlay and separate-view checks passed.");
