import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { cn, Input, Select, Switch, TextArea } from "../../components/ui";
import { api } from "../../lib/api";
import { humanize } from "../../lib/format";
import type { FieldSpec } from "../../lib/schema";

const MODEL_FIELDS = new Set(["embedding.model", "generation.model"]);

/** Range problem for a number field, in plain words, or null. */
export function rangeProblem(field: FieldSpec, value: unknown): string | null {
  if (field.kind !== "int" && field.kind !== "float") return null;
  if (typeof value !== "number" || Number.isNaN(value)) return "Enter a number.";
  if (field.kind === "int" && !Number.isInteger(value)) return "Use a whole number.";
  if (field.min !== undefined && value < field.min) return `Use ${field.min} or more.`;
  if (field.max !== undefined && value > field.max) return `Use ${field.max} or less.`;
  return null;
}

function ModelSuggestions({ provider, id }: { provider: string; id: string }) {
  const models = useQuery({
    queryKey: ["models", provider],
    queryFn: () => api.models(provider),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <datalist id={id}>
      {models.data?.models.map((model) => (
        <option key={model} value={model} />
      ))}
    </datalist>
  );
}

function NumberInput({
  id,
  field,
  value,
  invalid,
  describedBy,
  onChange,
}: {
  id: string;
  field: FieldSpec;
  value: number;
  invalid: boolean;
  describedBy: string;
  onChange: (v: number) => void;
}) {
  const [text, setText] = useState(String(value ?? ""));
  return (
    <Input
      id={id}
      type="number"
      inputMode={field.kind === "int" ? "numeric" : "decimal"}
      min={field.min}
      max={field.max}
      step={field.kind === "int" ? 1 : 0.01}
      value={text}
      aria-invalid={invalid}
      aria-describedby={describedBy}
      onChange={(event) => {
        setText(event.target.value);
        onChange(event.target.value === "" ? Number.NaN : Number(event.target.value));
      }}
      className="w-40 font-mono"
    />
  );
}

export function SchemaField({
  field,
  value,
  provider,
  error,
  changed,
  onChange,
}: {
  field: FieldSpec;
  value: unknown;
  provider?: string;
  error?: string | null;
  changed: boolean;
  onChange: (value: unknown) => void;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const path = `${field.stage}.${field.key}`;
  const invalid = Boolean(error);
  let control: React.ReactNode;
  switch (field.kind) {
    case "bool":
      control = <Switch checked={Boolean(value)} onChange={onChange} label={field.label} />;
      break;
    case "enum":
      control = (
        <Select
          id={id}
          value={String(value ?? "")}
          aria-describedby={hintId}
          onChange={(e) => onChange(e.target.value)}
          className="w-64"
        >
          {field.options.map((option) => (
            <option key={option} value={option}>
              {humanize(option)}
            </option>
          ))}
        </Select>
      );
      break;
    case "multi": {
      const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
      control = (
        <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0" aria-labelledby={id} aria-describedby={hintId}>
          {field.options.map((option) => {
            const on = selected.has(option);
            return (
              <button
                key={option}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  const next = new Set(selected);
                  if (on) next.delete(option);
                  else next.add(option);
                  onChange(field.options.filter((item) => next.has(item)));
                }}
                className={cn(
                  "press h-7 cursor-default rounded-full px-3 text-sm",
                  on ? "bg-accent-soft font-medium text-accent" : "bg-plinth text-ink hover:bg-rule",
                )}
              >
                {option.startsWith(".") ? option.slice(1).toUpperCase() : humanize(option)}
              </button>
            );
          })}
        </fieldset>
      );
      break;
    }
    case "list":
      control = (
        <Input
          id={id}
          aria-describedby={hintId}
          value={Array.isArray(value) ? (value as string[]).join(", ") : ""}
          placeholder="Separate with commas"
          onChange={(e) =>
            onChange(
              e.target.value
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean),
            )
          }
          className="font-mono"
        />
      );
      break;
    case "int":
    case "float":
      control = (
        <NumberInput
          id={id}
          field={field}
          value={value as number}
          invalid={invalid}
          describedBy={hintId}
          onChange={onChange}
        />
      );
      break;
    case "longtext":
      control = (
        <TextArea
          id={id}
          aria-describedby={hintId}
          rows={field.key === "prompt_template" ? 9 : 4}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="font-mono text-xs"
        />
      );
      break;
    default:
      control = (
        <>
          <Input
            id={id}
            aria-describedby={hintId}
            aria-invalid={invalid}
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            list={MODEL_FIELDS.has(path) ? `${id}-models` : undefined}
            className={cn("max-w-xl", field.key === "path" || MODEL_FIELDS.has(path) ? "font-mono" : "")}
          />
          {MODEL_FIELDS.has(path) && provider && <ModelSuggestions provider={provider} id={`${id}-models`} />}
        </>
      );
  }
  const range =
    (field.kind === "int" || field.kind === "float") && field.min !== undefined && field.max !== undefined
      ? `From ${field.min} to ${field.max}.`
      : "";

  return (
    <div className="grid gap-x-8 gap-y-2 py-3.5 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] md:items-start">
      <div>
        <label
          htmlFor={id}
          id={field.kind === "multi" ? id : undefined}
          className="flex items-center gap-2 text-base font-medium"
        >
          {field.label}
          {changed && <span className="rounded-full bg-accent-soft px-2 text-xs font-medium text-accent">Edited</span>}
        </label>
        <p id={hintId} className="mt-0.5 text-sm text-slate">
          {field.help} {range}
        </p>
      </div>
      <div className="min-w-0 space-y-1.5 md:pt-0.5">
        {control}
        {error && (
          <p role="alert" className="text-sm font-medium text-bad">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
