// A form generated from an action's parameter JSON schema.

import { useState, type FormEvent } from "react";

import type { JsonSchema } from "../lib/types";
import { Button } from "./ui";

export type Values = Record<string, unknown>;

/** Turn the form's strings into what the schema asks for; drop empty optional fields. */
export function coerce(schema: JsonSchema, raw: Record<string, string | boolean>): { values: Values; errors: Record<string, string> } {
  const values: Values = {};
  const errors: Record<string, string> = {};
  const required = new Set(schema.required ?? []);
  for (const [name, field] of Object.entries(schema.properties ?? {})) {
    const input = raw[name];
    if (field.type === "boolean") {
      values[name] = Boolean(input);
      continue;
    }
    const text = typeof input === "string" ? input.trim() : "";
    if (text === "") {
      if (required.has(name)) errors[name] = "required";
      continue;
    }
    if (field.type === "integer" || field.type === "number") {
      const n = Number(text);
      if (!Number.isFinite(n) || (field.type === "integer" && !Number.isInteger(n))) {
        errors[name] = field.type === "integer" ? "a whole number" : "a number";
        continue;
      }
      values[name] = n;
      continue;
    }
    if (field.minLength && text.length < field.minLength) {
      errors[name] = `at least ${field.minLength} characters`;
      continue;
    }
    values[name] = text;
  }
  return { values, errors };
}

function label(name: string): string {
  return name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase());
}

export function ParamForm({
  schema,
  fixed = {},
  submitLabel,
  busy,
  onSubmit,
}: {
  schema: JsonSchema;
  fixed?: Values; // shown, not editable (the object the action is about)
  submitLabel: string;
  busy?: boolean;
  onSubmit: (values: Values) => void;
}) {
  const fields = Object.entries(schema.properties ?? {});
  const required = new Set(schema.required ?? []);
  const [raw, setRaw] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(fields.map(([name, f]) => [name, fixed[name] !== undefined ? String(fixed[name]) : f.type === "boolean" ? false : ""])),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const result = coerce(schema, { ...raw, ...Object.fromEntries(Object.entries(fixed).map(([k, v]) => [k, String(v)])) });
    setErrors(result.errors);
    if (Object.keys(result.errors).length === 0) onSubmit(result.values);
  }

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      {fields.map(([name, field]) => {
        const id = `param-${name}`;
        const isFixed = fixed[name] !== undefined;
        const common =
          "mt-1 w-full rounded-md border border-stone-300 bg-white px-2 py-1.5 text-sm dark:border-stone-700 dark:bg-stone-950 disabled:opacity-70";
        return (
          <div key={name}>
            <label htmlFor={id} className="block text-sm font-medium">
              {field.title ?? label(name)}
              {required.has(name) && <span className="text-red-600"> *</span>}
            </label>
            {field.type === "boolean" ? (
              <input
                id={id}
                type="checkbox"
                className="mt-1"
                checked={Boolean(raw[name])}
                disabled={isFixed}
                onChange={(e) => setRaw({ ...raw, [name]: e.target.checked })}
              />
            ) : field.enum ? (
              <select id={id} className={common} value={String(raw[name] ?? "")} disabled={isFixed} onChange={(e) => setRaw({ ...raw, [name]: e.target.value })}>
                <option value="">{required.has(name) ? "Choose…" : "—"}</option>
                {field.enum.map((option) => (
                  <option key={String(option)} value={String(option)}>
                    {String(option)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                className={common}
                inputMode={field.type === "integer" || field.type === "number" ? "decimal" : undefined}
                value={String(raw[name] ?? "")}
                disabled={isFixed}
                aria-invalid={errors[name] ? true : undefined}
                aria-describedby={errors[name] ? `${id}-error` : undefined}
                onChange={(e) => setRaw({ ...raw, [name]: e.target.value })}
              />
            )}
            {field.description && <p className="mt-0.5 text-xs text-stone-500">{field.description}</p>}
            {errors[name] && (
              <p id={`${id}-error`} className="mt-0.5 text-xs text-red-700 dark:text-red-400">
                {label(name)}: {errors[name]}
              </p>
            )}
          </div>
        );
      })}
      <Button type="submit" disabled={busy}>
        {busy ? "Working…" : submitLabel}
      </Button>
    </form>
  );
}
