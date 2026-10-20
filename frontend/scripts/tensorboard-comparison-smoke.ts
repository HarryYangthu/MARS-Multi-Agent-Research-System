import assert from "node:assert/strict";
import { canOverlayImages, comparisonUrl, exactRunFilter, type ComparisonSource } from "../src/lib/tensorboardComparison";

const source = (id: string): ComparisonSource => ({ id, name: id, label: id, is_baseline: id === "baseline",
  native: false, scalar_run: `logs/${id}.v1`, histogram_run: `weights/${id}`, image_run: `figures/${id}`,
  comparison_run: "figures/attempt1/comparison", gallery_run: "figures/attempt1/gallery",
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
assert.equal(scalarSettings.get("regexInput"), exactRunFilter(pair.map((item) => item.scalar_run)));
assert.equal(scalarSettings.get("_smoothingWeight"), "0");
assert(new RegExp(scalarSettings.get("tagFilter") || "").test("validation/RES_db"));
const metricFilter = new RegExp(scalarSettings.get("tagFilter") || "");
for (const tag of ["heldout/PIM_db", "heldout/RES_db", "heldout/APE_db", "heldout/PIM_MAX_db", "heldout/RES_MAX_db"]) {
  assert(metricFilter.test(tag));
}
assert(!metricFilter.test("another/heldout/RES_db"));
assert(!metricFilter.test("heldout/RES_db_extra"));
assert(canOverlayImages(pair, pair));
assert(!canOverlayImages(pair, [...pair, source("another-candidate")]));
assert(!canOverlayImages([baseline, { ...candidate, attempt: "different" }], pair));
const imageUrl = comparisonUrl("/api/tensorboard/view/key/", "images", [baseline], false);
const imageSettings = new URLSearchParams(imageUrl.split("&").slice(1).join("&"));
assert(new RegExp(imageSettings.get("tagFilter") || "").test(baseline.image_tags[0]));
assert(!new RegExp(imageSettings.get("tagFilter") || "").test(candidate.image_tags[0]));
assert.equal(imageSettings.get("regexInput"), exactRunFilter([baseline.image_run]));
const overlayImageUrl = comparisonUrl("/api/tensorboard/view/key/", "images", pair, true);
const overlayImageSettings = new URLSearchParams(overlayImageUrl.split("&").slice(1).join("&"));
assert.equal(overlayImageSettings.get("regexInput"), exactRunFilter([baseline.comparison_run]));
assert(!new RegExp(overlayImageSettings.get("regexInput") || "").test(baseline.image_run));
assert(new RegExp(overlayImageSettings.get("tagFilter") || "").test(baseline.comparison_tags[0]));
const nativePair = [
  { ...baseline, native: true, attempt: "baseline-job", scalar_run: "execution/local_commands/baseline/invocation/measurement/model/tensorboard", comparison_tags: [] },
  { ...candidate, native: true, attempt: "candidate-job", scalar_run: "execution/local_commands/candidate/invocation/measurement/model/tensorboard", comparison_tags: [] },
];
const nativeUrl = comparisonUrl("/api/tensorboard/view/key/", "scalars", nativePair, true);
const nativeSettings = new URLSearchParams(nativeUrl.split("&").slice(1).join("&"));
const nativeFilter = new RegExp(nativeSettings.get("regexInput") || "");
assert(nativePair.every((item) => nativeFilter.test(item.scalar_run)));
assert(!nativeFilter.test(nativePair[0].scalar_run + "/other"));
assert(!canOverlayImages(nativePair, nativePair));
const separateUrl = comparisonUrl("/api/tensorboard/view/key/", "scalars", [nativePair[0]], false);
const separateSettings = new URLSearchParams(separateUrl.split("&").slice(1).join("&"));
const separateFilter = new RegExp(separateSettings.get("regexInput") || "");
assert(separateFilter.test(nativePair[0].scalar_run));
assert(!separateFilter.test(nativePair[1].scalar_run));
console.log("TensorBoard source selection, overlay and separate-view checks passed.");
