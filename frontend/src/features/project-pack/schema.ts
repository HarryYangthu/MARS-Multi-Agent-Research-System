import type {
  DynamicProjectPackUiSchema,
  JsonObject,
  JsonPrimitive,
  JsonValue,
  ProjectPackFieldSchema,
  ProjectPackFieldType,
  ProjectPackValidationIssue,
} from "./types";

const FIELD_TYPES = new Set<ProjectPackFieldType>([
  "string",
  "number",
  "integer",
  "boolean",
  "array",
  "object",
]);

export function normalizeProjectPackUiSchema(
  value: unknown,
): DynamicProjectPackUiSchema {
  const source = requireRecord(value, "Project Pack UI Schema");
  if (source.type !== "object") {
    throw new Error("Project Pack UI Schema root must use type=object");
  }
  const rawProperties = requireRecord(source.properties, "properties");
  const properties: Record<string, ProjectPackFieldSchema> = {};
  for (const [name, rawField] of Object.entries(rawProperties)) {
    properties[name] = normalizeField(rawField, `properties.${name}`);
  }
  return {
    type: "object",
    title: optionalString(source.title),
    description: optionalString(source.description),
    required: stringArray(source.required),
    properties,
  };
}

export function initialProjectPackValues(
  schema: DynamicProjectPackUiSchema,
): JsonObject {
  const values: JsonObject = {};
  for (const [name, field] of Object.entries(schema.properties)) {
    const initial = initialFieldValue(field);
    if (initial !== undefined) values[name] = initial;
  }
  return values;
}

export function validateProjectPackValues(
  schema: DynamicProjectPackUiSchema,
  values: JsonObject,
): ProjectPackValidationIssue[] {
  return validateObject(schema.properties, schema.required ?? [], values, "");
}

export function valueAtPath(
  values: JsonObject,
  path: readonly string[],
): JsonValue | undefined {
  let current: JsonValue = values;
  for (const segment of path) {
    if (!isRecord(current)) return undefined;
    current = current[segment];
    if (current === undefined) return undefined;
  }
  return current;
}

export function setValueAtPath(
  values: JsonObject,
  path: readonly string[],
  value: JsonValue,
): JsonObject {
  if (path.length === 0) return values;
  const [head, ...tail] = path;
  if (tail.length === 0) return { ...values, [head]: value };
  const existing = values[head];
  const child = isRecord(existing) ? existing : {};
  return { ...values, [head]: setValueAtPath(child, tail, value) };
}

export function parseArrayInput(
  raw: string,
  items: ProjectPackFieldSchema | undefined,
): JsonValue[] {
  const parts = raw
    .split(/[\n,]/)
    .map((part) => part.trim())
    .filter(Boolean);
  if (items?.type === "integer") {
    return parts.map((part) => Number.parseInt(part, 10)).filter(Number.isFinite);
  }
  if (items?.type === "number") {
    return parts.map(Number).filter(Number.isFinite);
  }
  return parts;
}

export function displayArrayInput(value: JsonValue | undefined): string {
  return Array.isArray(value)
    ? value
        .filter((item): item is JsonPrimitive => !Array.isArray(item) && !isRecord(item))
        .map(String)
        .join(", ")
    : "";
}

function normalizeField(value: unknown, path: string): ProjectPackFieldSchema {
  const source = requireRecord(value, path);
  const inferredType = source.enum ? "string" : source.properties ? "object" : "string";
  const rawType = optionalString(source.type) ?? inferredType;
  if (!FIELD_TYPES.has(rawType as ProjectPackFieldType)) {
    throw new Error(`${path}.type is not supported: ${rawType}`);
  }
  const type = rawType as ProjectPackFieldType;
  const field: ProjectPackFieldSchema = {
    type,
    title: optionalString(source.title),
    description: optionalString(source.description),
    enum: primitiveArray(source.enum),
    enumNames: stringArray(source.enumNames ?? source["x-enumNames"]),
    minimum: optionalNumber(source.minimum),
    maximum: optionalNumber(source.maximum),
    minLength: optionalNumber(source.minLength),
    maxLength: optionalNumber(source.maxLength),
    format: optionalString(source.format),
    widget: optionalString(source["x-ui-widget"] ?? source.widget),
    required: stringArray(source.required),
  };
  const defaultValue = jsonValue(source.default);
  if (defaultValue !== undefined) field.default = defaultValue;
  if (type === "object") {
    const nested = source.properties === undefined ? {} : requireRecord(source.properties, path);
    field.properties = Object.fromEntries(
      Object.entries(nested).map(([name, raw]) => [
        name,
        normalizeField(raw, `${path}.properties.${name}`),
      ]),
    );
  }
  if (type === "array" && source.items !== undefined) {
    field.items = normalizeField(source.items, `${path}.items`);
  }
  return field;
}

function initialFieldValue(field: ProjectPackFieldSchema): JsonValue | undefined {
  if (field.default !== undefined) return structuredClone(field.default);
  if (field.type === "object") {
    const nested: JsonObject = {};
    for (const [name, child] of Object.entries(field.properties ?? {})) {
      const initial = initialFieldValue(child);
      if (initial !== undefined) nested[name] = initial;
    }
    return nested;
  }
  if (field.type === "boolean") return false;
  if (field.type === "array") return [];
  if (field.enum?.length) return field.enum[0];
  return undefined;
}

function validateObject(
  properties: Record<string, ProjectPackFieldSchema>,
  required: readonly string[],
  values: JsonObject,
  prefix: string,
): ProjectPackValidationIssue[] {
  const issues: ProjectPackValidationIssue[] = [];
  const requiredNames = new Set(required);
  for (const [name, field] of Object.entries(properties)) {
    const path = prefix ? `${prefix}.${name}` : name;
    const value = values[name];
    if (requiredNames.has(name) && isMissing(value)) {
      issues.push({ path, message: "此字段为必填项" });
      continue;
    }
    if (value === undefined || value === null || value === "") continue;
    if (field.type === "number" || field.type === "integer") {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        issues.push({ path, message: "请输入有效数字" });
        continue;
      }
      if (field.type === "integer" && !Number.isInteger(value)) {
        issues.push({ path, message: "请输入整数" });
      }
      if (field.minimum !== undefined && value < field.minimum) {
        issues.push({ path, message: `不得小于 ${field.minimum}` });
      }
      if (field.maximum !== undefined && value > field.maximum) {
        issues.push({ path, message: `不得大于 ${field.maximum}` });
      }
    }
    if (field.type === "string" && typeof value === "string") {
      if (field.minLength !== undefined && value.length < field.minLength) {
        issues.push({ path, message: `至少输入 ${field.minLength} 个字符` });
      }
      if (field.maxLength !== undefined && value.length > field.maxLength) {
        issues.push({ path, message: `最多输入 ${field.maxLength} 个字符` });
      }
    }
    if (field.type === "object" && isRecord(value)) {
      issues.push(
        ...validateObject(field.properties ?? {}, field.required ?? [], value, path),
      );
    }
  }
  return issues;
}

function isMissing(value: JsonValue | undefined): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  return value;
}

function isRecord(value: unknown): value is JsonObject & Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function optionalNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((item): item is string => typeof item === "string");
}

function primitiveArray(value: unknown): JsonPrimitive[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter(
    (item): item is JsonPrimitive =>
      item === null || ["string", "number", "boolean"].includes(typeof item),
  );
  return items.length ? items : undefined;
}

function jsonValue(value: unknown): JsonValue | undefined {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    const output: JsonValue[] = [];
    for (const item of value) {
      const parsed = jsonValue(item);
      if (parsed === undefined) return undefined;
      output.push(parsed);
    }
    return output;
  }
  if (isRecord(value)) {
    const output: JsonObject = {};
    for (const [key, item] of Object.entries(value)) {
      const parsed = jsonValue(item);
      if (parsed === undefined) return undefined;
      output[key] = parsed;
    }
    return output;
  }
  return undefined;
}
