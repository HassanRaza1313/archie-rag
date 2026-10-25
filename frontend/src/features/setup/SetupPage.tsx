import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, cn, ErrorNote, GalleryLabel, PageHeader, Select, Skeleton, StatusBadge } from "../../components/ui";
import { api, type Config, type ValidationResult } from "../../lib/api";
import {
  applyPreset,
  buildStages,
  changedFields,
  type FieldSpec,
  getField,
  groupStages,
  plainError,
  primaryAction,
  type StageSpec,
  setField,
} from "../../lib/schema";
import { rangeProblem, SchemaField } from "./SchemaField";

const ENVIRONMENT = /unreachable|not available at|outside the allowed|does not exist/i;

const PRESET_NAMES: Record<string, string> = {
  balanced: "Balanced",
  high_accuracy: "Most careful",
  low_latency: "Fastest",
};

function UploadPanel({ kbId }: { kbId: string }) {
  const queryClient = useQueryClient();
  const uploads = useQuery({ queryKey: ["uploads", kbId], queryFn: () => api.uploads(kbId) });
  const upload = useMutation({
    mutationFn: (files: File[]) => api.upload(kbId, files),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["uploads", kbId] }),
  });
  return (
    <div className="border-t border-rule py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="font-semibold">Uploaded files</div>
          <div className="text-sm text-slate">Rebuild after adding files so answers include them.</div>
        </div>
        <label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-lg border border-rule-strong bg-panel px-4 text-sm font-semibold hover:border-ink">
          <Upload className="size-4" aria-hidden /> Add files
          <input
            type="file"
            multiple
            accept=".pdf,.md,.txt"
            className="sr-only"
            onChange={(event) => event.target.files && upload.mutate(Array.from(event.target.files))}
          />
        </label>
      </div>
      <ErrorNote error={upload.error} />
      <ul className="mt-4 grid gap-2 sm:grid-cols-2">
        {uploads.data?.map((file) => (
          <GalleryLabel as="li" key={file.name} title={file.name}>
            {(file.bytes / 1024).toFixed(1)} KB
          </GalleryLabel>
        ))}
      </ul>
      {uploads.data?.length === 0 && <p className="mt-3 text-sm text-slate">No files yet.</p>}
    </div>
  );
}

function StageRail({
  stages,
  active,
  onSelect,
  changed,
  problems,
}: {
  stages: StageSpec[];
  active: string;
  onSelect: (key: string) => void;
  changed: Set<string>;
  problems: Set<string>;
}) {
  return (
    <nav
      aria-label="Settings by stage"
      className="flex gap-6 overflow-x-auto pb-2 lg:flex-col lg:gap-7 lg:overflow-visible"
    >
      {groupStages(stages).map((group) => (
        <div key={group.title} className="shrink-0">
          <div className="px-3 text-xs font-semibold text-ink">{group.title}</div>
          <div className="px-3 pb-2 text-xs text-slate">{group.note}</div>
          <div className="flex gap-1 lg:flex-col">
            {group.stages.map((stage) => {
              const edited = [...changed].some((path) => path.startsWith(`${stage.key}.`));
              const broken = problems.has(stage.key);
              return (
                <button
                  key={stage.key}
                  type="button"
                  onClick={() => onSelect(stage.key)}
                  aria-current={active === stage.key ? "page" : undefined}
                  className={cn(
                    "flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-left text-sm whitespace-nowrap transition-colors duration-150",
                    active === stage.key
                      ? "bg-plinth font-semibold text-ink"
                      : "text-slate hover:bg-plinth hover:text-ink",
                  )}
                >
                  <span className="lg:flex-1">{stage.title}</span>
                  {broken && <span className="text-xs font-semibold text-bad">Needs a fix</span>}
                  {!broken && edited && <span className="text-xs font-semibold text-accent">Edited</span>}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function fieldProblems(fields: FieldSpec[], draft: Config, server: ValidationResult | null): Map<string, string> {
  const map = new Map<string, string>();
  for (const error of server?.errors ?? []) {
    map.set(error.loc.slice(0, 2).join("."), plainError(error.msg));
  }
  for (const field of fields) {
    const problem = rangeProblem(field, getField(draft, field.stage, field.key));
    if (problem) map.set(`${field.stage}.${field.key}`, problem);
  }
  return map;
}

export function SetupPage() {
  const { kbId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const schema = useQuery({ queryKey: ["schema"], queryFn: api.schema, staleTime: Number.POSITIVE_INFINITY });
  const kb = useQuery({ queryKey: ["kb", kbId], queryFn: () => api.getKb(kbId) });
  const builtConfig = useQuery({
    queryKey: ["config", kbId, kb.data?.built_version],
    queryFn: () => api.configVersion(kbId, kb.data?.built_version as number),
    enabled: !!kb.data?.built_version,
  });
  const [draft, setDraft] = useState<Config | null>(null);
  const [activeStage, setActiveStage] = useState("source");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    if (kb.data && draft === null) setDraft(kb.data.config);
  }, [kb.data, draft]);

  // Validate as the user edits (debounced), so what the page shows always matches the current draft.
  useEffect(() => {
    if (!draft) return;
    setValidation(null);
    const timer = window.setTimeout(() => {
      api.validate(kbId, draft).then(setValidation, () => undefined);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [draft, kbId]);

  const stages = useMemo(() => (schema.data ? buildStages(schema.data.schema) : []), [schema.data]);
  const allFields = useMemo(() => stages.flatMap((stage) => stage.fields), [stages]);
  const indexStages = useMemo(() => new Set(schema.data?.index_stages ?? []), [schema.data]);
  const unsaved = useMemo(() => changedFields(kb.data?.config, draft ?? undefined), [kb.data, draft]);
  const problems = useMemo(
    () => (draft ? fieldProblems(allFields, draft, validation) : new Map<string, string>()),
    [allFields, draft, validation],
  );
  const problemStages = new Set([...problems.keys()].map((path) => path.split(".")[0] ?? ""));
  const differsFromBuilt = useMemo(() => {
    if (!builtConfig.data || !draft) return false;
    return [...changedFields(builtConfig.data, draft)].some((path) => indexStages.has(path.split(".")[0] ?? ""));
  }, [builtConfig.data, draft, indexStages]);

  // Environment problems (model server down, folder not allowed) block building, never saving.
  const environmental = [...problems.values()].filter((message) => ENVIRONMENT.test(message)).length;
  let action = primaryAction({
    problems: problems.size - environmental,
    unsaved,
    indexStages,
    status: kb.data?.status,
  });
  if (environmental && (action.kind === "save-rebuild" || action.kind === "build")) {
    action = {
      kind: "blocked",
      label: action.label,
      reason: `Fix ${environmental} problem${environmental === 1 ? "" : "s"} first`,
    };
  }

  const save = useMutation({
    mutationFn: async (thenBuild: boolean) => {
      if (unsaved.size) await api.saveConfig(kbId, draft as Config);
      if (thenBuild) return (await api.startBuild(kbId)).job_id;
      return null;
    },
    onSuccess: async (jobId) => {
      await queryClient.invalidateQueries({ queryKey: ["kb", kbId] });
      if (jobId) {
        navigate(`/kb/${kbId}/builds/${jobId}`);
        return;
      }
      setDraft((await api.getKb(kbId)).config);
      setSaved("Saved. The next question uses these settings.");
    },
  });
  const preset = useMutation({
    mutationFn: (name: string) => api.preset(name),
    onSuccess: (config) => draft && setDraft(applyPreset(draft, config)),
  });

  if (schema.isLoading || kb.isLoading || !draft) {
    return (
      <div className="px-5 py-10 md:px-10">
        <Skeleton lines={5} />
      </div>
    );
  }
  if (schema.error || kb.error) {
    return (
      <div className="p-10">
        <ErrorNote error={schema.error ?? kb.error} />
      </div>
    );
  }

  const stage = stages.find((item) => item.key === activeStage) ?? stages[0];
  if (!stage) return null;
  const essentials = stage.fields.filter((field) => field.tier === "essential");
  const advanced = stage.fields.filter((field) => field.tier === "advanced");
  const field = (item: FieldSpec) => (
    <SchemaField
      key={item.key}
      field={item}
      value={getField(draft, item.stage, item.key)}
      provider={String(getField(draft, item.stage, "provider") ?? "")}
      changed={unsaved.has(`${item.stage}.${item.key}`)}
      error={problems.get(`${item.stage}.${item.key}`)}
      onChange={(value) => {
        setSaved(null);
        setDraft(setField(draft, item.stage, item.key, value));
      }}
    />
  );
  const warnings = validation?.warnings ?? [];

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            Setup <StatusBadge status={kb.data?.status} />
          </span>
        }
        description={
          kb.data?.status === "draft"
            ? "The recommended settings are already on. You can build now, or tune any stage first."
            : "Settings grouped by what they change. Every setting explains itself."
        }
        actions={
          <Select
            aria-label="Start from a preset"
            value=""
            onChange={(event) => event.target.value && preset.mutate(event.target.value)}
            className="w-56"
          >
            <option value="">Start from a preset…</option>
            {schema.data?.presets.map((item) => (
              <option key={item.name} value={item.name}>
                {PRESET_NAMES[item.name] ?? item.name}
              </option>
            ))}
          </Select>
        }
      />
      {differsFromBuilt && (
        <div className="mx-5 mb-2 rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn md:mx-10">
          These settings change how documents are read. Chat keeps using the current version until you rebuild.
        </div>
      )}
      <div className="grid flex-1 gap-6 px-5 pb-10 md:px-10 lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-12">
        <StageRail
          stages={stages}
          active={stage.key}
          onSelect={(key) => {
            setActiveStage(key);
            setShowAdvanced(false);
          }}
          changed={unsaved}
          problems={problemStages}
        />
        <section aria-labelledby="stage-title" className="min-w-0">
          <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule pb-4">
            <div>
              <h2 id="stage-title" className="text-lg font-semibold">
                {stage.title}
              </h2>
              <p className="mt-1 text-sm text-slate">{stage.summary}</p>
            </div>
            <span className={cn("text-xs font-semibold", stage.rebuild ? "text-warn" : "text-ok")}>
              {stage.rebuild ? "Changes need a rebuild" : "Changes apply to the next question"}
            </span>
          </div>
          {warnings
            .filter((warning) => warning.stage === stage.key)
            .map((warning) => (
              <p key={warning.msg} className="mt-4 rounded-lg bg-warn-soft px-3 py-2 text-sm text-ink">
                {warning.msg}
              </p>
            ))}
          <div className="divide-y divide-rule">{essentials.map(field)}</div>
          {essentials.length === 0 && (
            <p className="py-6 text-sm text-slate">
              The defaults suit most collections. Open the advanced settings to tune.
            </p>
          )}
          {advanced.length > 0 && (
            <div className="border-t border-rule">
              <button
                type="button"
                aria-expanded={showAdvanced}
                onClick={() => setShowAdvanced((value) => !value)}
                className="flex h-12 w-full items-center gap-2 text-sm font-semibold text-slate hover:text-ink"
              >
                <ChevronDown className={cn("size-4 transition-transform duration-150", showAdvanced && "rotate-180")} />
                {showAdvanced ? "Hide advanced settings" : `Advanced settings (${advanced.length})`}
              </button>
              {showAdvanced && <div className="divide-y divide-rule border-t border-rule">{advanced.map(field)}</div>}
            </div>
          )}
          {stage.key === "source" && getField(draft, "source", "type") === "uploads" && <UploadPanel kbId={kbId} />}
        </section>
      </div>
      <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 border-t border-rule bg-panel px-5 py-3 md:px-10">
        <p className="text-sm text-slate" aria-live="polite">
          {action.kind === "blocked"
            ? `${action.reason}. Look for “Needs a fix” in the list.`
            : (saved ??
              (unsaved.size
                ? `${unsaved.size} unsaved change${unsaved.size === 1 ? "" : "s"}`
                : kb.data?.built_version
                  ? `Version ${kb.data.active_version} · built from version ${kb.data.built_version}`
                  : "Not built yet"))}
        </p>
        <div className="flex items-center gap-2">
          <ErrorNote error={save.error ?? preset.error} />
          {unsaved.size > 0 && (
            <Button variant="ghost" onClick={() => setDraft(kb.data?.config ?? draft)}>
              Discard changes
            </Button>
          )}
          {action.kind !== "none" && (
            <Button
              variant="primary"
              busy={save.isPending}
              disabled={action.kind === "blocked" || kb.data?.status === "building"}
              onClick={() => save.mutate(action.kind === "save-rebuild" || action.kind === "build")}
            >
              {action.label}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
