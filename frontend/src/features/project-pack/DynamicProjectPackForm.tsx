"use client";

import type { ChangeEvent, ReactNode } from "react";

import {
  displayArrayInput,
  parseArrayInput,
  setValueAtPath,
  valueAtPath,
} from "./schema";
import type {
  DynamicProjectPackUiSchema,
  JsonObject,
  JsonPrimitive,
  JsonValue,
  ProjectPackFieldSchema,
  ProjectPackValidationIssue,
} from "./types";

interface DynamicProjectPackFormProps {
  schema: DynamicProjectPackUiSchema;
  values: JsonObject;
  issues?: ProjectPackValidationIssue[];
  disabled?: boolean;
  onChange: (values: JsonObject) => void;
}

export function DynamicProjectPackForm({
  schema,
  values,
  issues = [],
  disabled = false,
  onChange,
}: DynamicProjectPackFormProps): JSX.Element {
  const issueMap = new Map(issues.map((issue) => [issue.path, issue.message]));
  const update = (path: readonly string[], value: JsonValue): void => {
    onChange(setValueAtPath(values, path, value));
  };

  return (
    <section className="rounded-2xl border border-mars-border bg-mars-panel p-5 shadow-xl shadow-black/10">
      <div className="mb-5">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-300">
          Project Pack · UI Schema
        </p>
        <h2 className="mt-2 text-lg font-semibold text-slate-100">
          {schema.title ?? "Project inputs"}
        </h2>
        {schema.description ? (
          <p className="mt-1 text-sm leading-6 text-slate-400">{schema.description}</p>
        ) : null}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {Object.entries(schema.properties).map(([name, field]) => (
          <FieldRenderer
            key={name}
            name={name}
            path={[name]}
            field={field}
            values={values}
            required={(schema.required ?? []).includes(name)}
            disabled={disabled}
            issueMap={issueMap}
            onChange={update}
          />
        ))}
      </div>
    </section>
  );
}

interface FieldRendererProps {
  name: string;
  path: readonly string[];
  field: ProjectPackFieldSchema;
  values: JsonObject;
  required: boolean;
  disabled: boolean;
  issueMap: Map<string, string>;
  onChange: (path: readonly string[], value: JsonValue) => void;
}

function FieldRenderer({
  name,
  path,
  field,
  values,
  required,
  disabled,
  issueMap,
  onChange,
}: FieldRendererProps): JSX.Element {
  const pathKey = path.join(".");
  const value = valueAtPath(values, path);
  if (field.type === "object") {
    return (
      <fieldset className="rounded-xl border border-mars-border bg-mars-bg/40 p-4 md:col-span-2">
        <legend className="px-2 text-sm font-semibold text-slate-200">
          {field.title ?? humanize(name)}
        </legend>
        {field.description ? (
          <p className="mb-3 text-xs leading-5 text-slate-400">{field.description}</p>
        ) : null}
        <div className="grid gap-4 md:grid-cols-2">
          {Object.entries(field.properties ?? {}).map(([childName, child]) => (
            <FieldRenderer
              key={childName}
              name={childName}
              path={[...path, childName]}
              field={child}
              values={values}
              required={(field.required ?? []).includes(childName)}
              disabled={disabled}
              issueMap={issueMap}
              onChange={onChange}
            />
          ))}
        </div>
      </fieldset>
    );
  }

  const error = issueMap.get(pathKey);
  return (
    <FieldShell
      label={field.title ?? humanize(name)}
      description={field.description}
      required={required}
      error={error}
    >
      {renderInput({ field, value, disabled, path, onChange })}
    </FieldShell>
  );
}

function renderInput({
  field,
  value,
  disabled,
  path,
  onChange,
}: {
  field: ProjectPackFieldSchema;
  value: JsonValue | undefined;
  disabled: boolean;
  path: readonly string[];
  onChange: (path: readonly string[], value: JsonValue) => void;
}): ReactNode {
  if (field.enum?.length) {
    return (
      <select
        className={inputClass()}
        value={encodePrimitive(value)}
        disabled={disabled}
        onChange={(event) => onChange(path, decodePrimitive(event.target.value))}
      >
        {field.enum.map((option, index) => (
          <option key={encodePrimitive(option)} value={encodePrimitive(option)}>
            {field.enumNames?.[index] ?? String(option)}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "boolean") {
    return (
      <label className="flex min-h-11 items-center gap-3 rounded-lg border border-mars-border bg-mars-bg/70 px-3 text-sm text-slate-200">
        <input
          type="checkbox"
          checked={value === true}
          disabled={disabled}
          onChange={(event) => onChange(path, event.target.checked)}
          className="h-4 w-4 accent-indigo-500"
        />
        {value === true ? "Enabled" : "Disabled"}
      </label>
    );
  }
  if (field.type === "array") {
    return (
      <textarea
        className={`${inputClass()} min-h-24 resize-y`}
        value={displayArrayInput(value)}
        disabled={disabled}
        onChange={(event) => onChange(path, parseArrayInput(event.target.value, field.items))}
      />
    );
  }
  if (field.type === "number" || field.type === "integer") {
    return (
      <input
        type="number"
        className={inputClass()}
        value={typeof value === "number" ? value : ""}
        min={field.minimum}
        max={field.maximum}
        step={field.type === "integer" ? 1 : "any"}
        disabled={disabled}
        onChange={(event) => onChange(path, numberValue(event, field.type === "integer"))}
      />
    );
  }
  if (field.widget === "textarea" || field.format === "multiline") {
    return (
      <textarea
        className={`${inputClass()} min-h-28 resize-y`}
        value={typeof value === "string" ? value : ""}
        minLength={field.minLength}
        maxLength={field.maxLength}
        disabled={disabled}
        onChange={(event) => onChange(path, event.target.value)}
      />
    );
  }
  return (
    <input
      type="text"
      className={inputClass()}
      value={typeof value === "string" ? value : ""}
      minLength={field.minLength}
      maxLength={field.maxLength}
      disabled={disabled}
      onChange={(event) => onChange(path, event.target.value)}
    />
  );
}

function FieldShell({
  label,
  description,
  required,
  error,
  children,
}: {
  label: string;
  description?: string;
  required: boolean;
  error?: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <label className="block space-y-2">
      <span className="flex items-center gap-2 text-sm font-medium text-slate-200">
        {label}
        {required ? <span className="text-rose-400">*</span> : null}
      </span>
      {children}
      {description ? <span className="block text-xs text-slate-500">{description}</span> : null}
      {error ? <span className="block text-xs text-rose-300">{error}</span> : null}
    </label>
  );
}

function inputClass(): string {
  return "w-full rounded-lg border border-mars-border bg-mars-bg/80 px-3 py-2.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-600 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50";
}

function numberValue(
  event: ChangeEvent<HTMLInputElement>,
  integer: boolean,
): JsonValue {
  if (!event.target.value) return null;
  const parsed = integer
    ? Number.parseInt(event.target.value, 10)
    : Number(event.target.value);
  return Number.isFinite(parsed) ? parsed : null;
}

function encodePrimitive(value: JsonValue | undefined): string {
  if (value === undefined || Array.isArray(value) || isObject(value)) return "";
  return JSON.stringify(value);
}

function decodePrimitive(value: string): JsonPrimitive {
  if (!value) return "";
  const parsed = JSON.parse(value) as unknown;
  if (
    parsed === null ||
    typeof parsed === "string" ||
    typeof parsed === "number" ||
    typeof parsed === "boolean"
  ) {
    return parsed;
  }
  return value;
}

function isObject(value: JsonValue): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
