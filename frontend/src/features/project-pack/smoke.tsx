import { renderToStaticMarkup } from "react-dom/server";

import { normalizeIdeaDiscoveryPayload } from "../idea-discovery/normalize";
import { syntheticDiscoveryPayloadFixture } from "../idea-discovery/fixtures/synthetic-discovery";
import {
  DISCOVERY_STAGES,
  DiscoveryStagePanel,
} from "../idea-discovery/DiscoveryStagePanels";
import { DynamicProjectPackForm } from "./DynamicProjectPackForm";
import { syntheticRegressionUiSchemaFixture } from "./fixtures/synthetic-regression-pack";
import {
  initialProjectPackValues,
  normalizeProjectPackUiSchema,
  validateProjectPackValues,
} from "./schema";

const schema = normalizeProjectPackUiSchema(syntheticRegressionUiSchemaFixture);
const values = initialProjectPackValues(schema);
const issues = validateProjectPackValues(schema, values);
assert(issues.length === 0, `fixture defaults should validate: ${JSON.stringify(issues)}`);

const html = renderToStaticMarkup(
  <DynamicProjectPackForm schema={schema} values={values} onChange={() => undefined} />,
);
for (const expected of ["Dataset kind", "Feature count", "Target metrics", "Seed count"]) {
  assert(html.includes(expected), `dynamic form did not render ${expected}`);
}

const discovery = normalizeIdeaDiscoveryPayload(syntheticDiscoveryPayloadFixture);
assert(discovery.run_id === "synthetic-run-001", "run id should survive normalization");
assert(discovery.hypotheses.length === 2, "hypothesis records should normalize");
assert(discovery.matches.length === 1, "pairwise records should normalize");
assert(discovery.proximity_graphs.length === 1, "proximity records should normalize");
assert(discovery.meta_reviews.length === 1, "meta-review records should normalize");
assert(discovery.finalist_ids[0] === "h-evolved-b", "Top-K order should remain authoritative");
for (const stage of DISCOVERY_STAGES) {
  const stageHtml = renderToStaticMarkup(
    <DiscoveryStagePanel stage={stage.id} snapshot={discovery} />,
  );
  assert(stageHtml.length > 100, `${stage.id} stage should render from the REST fixture`);
}

process.stdout.write("V3.1 frontend fixture smoke passed\n");

function assert(condition: boolean, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
