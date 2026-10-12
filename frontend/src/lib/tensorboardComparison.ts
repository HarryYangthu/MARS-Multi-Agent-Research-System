export type TensorBoardDashboard = "scalars" | "images" | "histograms" | "text";
export type ComparisonSource = {
  id: string;
  name: string;
  label: string;
  is_baseline: boolean;
  native: boolean;
  scalar_run: string;
  histogram_run: string;
  image_run: string;
  image_tags: string[];
  comparison_tags: string[];
  attempt: string;
  training_steps: number;
  validation_points: number;
};

const escapeRegex = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export const exactRunFilter = (values: string[]): string => `^(?:${Array.from(new Set(values)).map(escapeRegex).join("|")})$`;

export function comparisonUrl(base: string, dashboard: TensorBoardDashboard, sources: ComparisonSource[], overlay: boolean): string {
  const runNames = sources.map((source) => dashboard === "histograms" ? source.histogram_run : dashboard === "images" ? source.image_run : source.scalar_run);
  const values = new URLSearchParams({ regexInput: exactRunFilter(runNames), _smoothingWeight: "0" });
  if (dashboard === "scalars") values.set("tagFilter", "^(train/loss|train/learning_rate|validation/RES_db|test/RES_db)$");
  if (dashboard === "images") {
    const tags = overlay && sources.length > 1 ? sources[0].comparison_tags : sources.flatMap((source) => source.image_tags);
    // An impossible filter keeps unavailable images visibly empty rather than
    // silently substituting figures from unrelated experiments.
    values.set("tagFilter", tags.length ? exactRunFilter(tags) : "(?!)");
  }
  return `${base}#${dashboard}&${values.toString()}`;
}

export function canOverlayImages(sources: ComparisonSource[], allSources: ComparisonSource[]): boolean {
  if (sources.length !== 2 || sources[0].attempt !== sources[1].attempt) return false;
  // A static combined figure is applicable only to its exact two-model attempt.
  return sources[0].comparison_tags.length > 0 && allSources.filter((source) => source.attempt === sources[0].attempt).length === 2;
}
