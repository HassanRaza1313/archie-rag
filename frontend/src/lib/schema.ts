/**
 * Turns the backend's config JSON Schema into stage and field descriptors for the schema-driven setup UI.
 * The backend is the single source of truth: adding a field there adds it here.
 */
import type { Config, JsonSchema } from "./api";

export type FieldKind = "enum" | "multi" | "bool" | "int" | "float" | "text" | "longtext" | "list";

export interface FieldSpec {
  stage: string;
  key: string;
  label: string;
  help: string;
  tier: "essential" | "advanced";
  kind: FieldKind;
  options: string[];
  min?: number;
  max?: number;
  defaultValue: unknown;
}

export interface StageSpec {
  key: string;
  title: string;
  summary: string;
  rebuild: boolean;
  fields: FieldSpec[];
}

function resolve(schema: JsonSchema, node: JsonSchema): JsonSchema {
  if (!node.$ref) return node;
  const name = node.$ref.split("/").pop() ?? "";
  return schema.$defs?.[name] ?? node;
}

function kindOf(key: string, node: JsonSchema): FieldKind {
  if (node.enum) return "enum";
  if (node.type === "array") return node.items?.enum ? "multi" : "list";
  if (node.type === "boolean") return "bool";
  if (node.type === "integer") return "int";
  if (node.type === "number") return "float";
  const long = typeof node.default === "string" && node.default.length > 80;
  return key.includes("prompt") || long ? "longtext" : "text";
}

export function buildStages(schema: JsonSchema): StageSpec[] {
  return Object.entries(schema.properties ?? {}).map(([stageKey, ref]) => {
    const stage = resolve(schema, ref);
    const fields = Object.entries(stage.properties ?? {}).map(([key, raw]): FieldSpec => {
      const node = resolve(schema, raw);
      return {
        stage: stageKey,
        key,
        label: node.title ?? key,
        help: node.description ?? "",
        tier: node.tier ?? "essential",
        kind: kindOf(key, node),
        options: node.enum ?? node.items?.enum ?? [],
        min: node.minimum,
        max: node.maximum,
        defaultValue: node.default,
      };
    });
    return {
      key: stageKey,
      title: stage.title ?? stageKey,
      summary: stage.summary ?? "",
      rebuild: Boolean(stage.rebuild),
      fields,
    };
  });
}

export function getField(config: Config, stage: string, key: string): unknown {
  return config[stage]?.[key];
}

export function setField(config: Config, stage: string, key: string, value: unknown): Config {
  return { ...config, [stage]: { ...config[stage], [key]: value } };
}

/** Fields whose values differ between two configs, as "stage.key" strings. */
export function changedFields(left: Config | undefined, right: Config | undefined): Set<string> {
  const changed = new Set<string>();
  if (!left || !right) return changed;
  for (const stage of new Set([...Object.keys(left), ...Object.keys(right)])) {
    const a = left[stage] ?? {};
    const b = right[stage] ?? {};
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (JSON.stringify(a[key]) !== JSON.stringify(b[key])) changed.add(`${stage}.${key}`);
    }
  }
  return changed;
}

/** Deep-merge a preset into a config, keeping the data source so applying a preset never loses the corpus. */
export function applyPreset(current: Config, preset: Config): Config {
  const merged: Config = {};
  for (const stage of Object.keys(preset)) merged[stage] = { ...current[stage], ...preset[stage] };
  if (current.source) merged.source = current.source;
  return merged;
}

export interface StageGroup {
  title: string;
  note: string;
  stages: StageSpec[];
}

/** Rail groups: what changes need a rebuild versus what applies to the next question. */
export function groupStages(stages: StageSpec[]): StageGroup[] {
  const operations = new Set(["cache", "privacy", "telemetry"]);
  return [
    {
      title: "What it reads",
      note: "Changes need a rebuild",
      stages: stages.filter((stage) => stage.rebuild),
    },
    {
      title: "How it answers",
      note: "Changes apply to the next question",
      stages: stages.filter((stage) => !stage.rebuild && !operations.has(stage.key)),
    },
    {
      title: "Safety and records",
      note: "Changes apply to the next question",
      stages: stages.filter((stage) => !stage.rebuild && operations.has(stage.key)),
    },
  ].filter((group) => group.stages.length > 0);
}

/** Validation messages in plain words ("Use 2 or less."), never raw validator text. */
export function plainError(message: string): string {
  const number = (text: string) => text.match(/-?\d+(\.\d+)?/)?.[0] ?? "";
  if (/less than or equal to/i.test(message)) return `Use ${number(message)} or less.`;
  if (/greater than or equal to/i.test(message)) return `Use ${number(message)} or more.`;
  if (/less than/i.test(message)) return `Use less than ${number(message)}.`;
  if (/greater than/i.test(message)) return `Use more than ${number(message)}.`;
  if (/valid integer/i.test(message)) return "Use a whole number.";
  if (/valid number|parse.*number/i.test(message)) return "Enter a number.";
  if (/at least \d+ character/i.test(message)) return "This can't be empty.";
  if (/^Value error, /.test(message))
    return message.replace(/^Value error, /, "").replace(/^\w/, (c) => c.toUpperCase());
  return message;
}

export type PrimaryAction =
  | { kind: "blocked"; label: string; reason: string }
  | { kind: "save"; label: string }
  | { kind: "save-rebuild"; label: string }
  | { kind: "build"; label: string }
  | { kind: "none"; label: string };

/** The one honest primary action for the Setup footer. */
export function primaryAction(input: {
  problems: number;
  unsaved: Set<string>;
  indexStages: Set<string>;
  status: string | undefined;
}): PrimaryAction {
  const { problems, unsaved, indexStages, status } = input;
  if (problems > 0) {
    return { kind: "blocked", label: "Save", reason: `Fix ${problems} problem${problems === 1 ? "" : "s"} first` };
  }
  const touchesIndex = [...unsaved].some((path) => indexStages.has(path.split(".")[0] ?? ""));
  const unbuilt = status === "draft" || status === "failed";
  if (unsaved.size && (touchesIndex || unbuilt))
    return { kind: "save-rebuild", label: unbuilt ? "Save and build" : "Save and rebuild" };
  if (unsaved.size) return { kind: "save", label: "Save changes" };
  if (unbuilt) return { kind: "build", label: "Build" };
  if (status === "stale") return { kind: "build", label: "Rebuild" };
  return { kind: "none", label: "All changes saved" };
}
