import { useMutation } from "@tanstack/react-query";
import { Check, FolderOpen, Upload } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, cn, ErrorNote, Input } from "../../components/ui";
import { ApiError, api, type Config } from "../../lib/api";
import { FolderPicker } from "./FolderPicker";
import { ModelCheck, type ModelChoice } from "./ModelCheck";

const PRESETS: { name: string; title: string; body: string }[] = [
  { name: "balanced", title: "Balanced", body: "Good answers at a comfortable speed. Best for most collections." },
  { name: "high_accuracy", title: "Most careful", body: "Checks more passages and double-checks answers. Slower." },
  { name: "low_latency", title: "Fastest", body: "Answers quickly from fewer passages. Good for simple questions." },
];

const STEPS = ["Name", "Documents", "Answers"] as const;
const ACCEPT = [
  ".pdf,.docx,.pptx,.xlsx,.html,.md,.txt,.csv,.tsv,.json,.eml,.epub,.odt,.ods,.odp",
  ".png,.jpg,.jpeg,.tif,.tiff,.webp,.bmp,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mov,.webm,.mkv",
].join(",");

/** A selectable row in a grouped list, with a checkmark when chosen (as in macOS lists and sheets). */
function Choice({
  checked,
  onSelect,
  title,
  body,
  icon,
  name,
}: {
  checked: boolean;
  onSelect: () => void;
  title: string;
  body: string;
  icon?: ReactNode;
  name: string;
}) {
  return (
    <label className="flex cursor-default items-center gap-3.5 px-4 py-3 hover:bg-plinth/60 has-[:focus-visible]:bg-accent-soft">
      <input type="radio" name={name} checked={checked} onChange={onSelect} className="sr-only" />
      {icon && (
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent [&>svg]:size-[18px]">
          {icon}
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block text-base font-medium">{title}</span>
        <span className="block text-sm text-slate">{body}</span>
      </span>
      <Check
        aria-hidden
        strokeWidth={2.5}
        className={cn("size-4 shrink-0 text-accent", checked ? "opacity-100" : "opacity-0")}
      />
    </label>
  );
}

function friendly(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return "A chatbot with this name already exists.";
  if (error instanceof ApiError && Array.isArray(error.detail)) return "Some details aren't valid yet.";
  return error instanceof Error ? error.message : String(error);
}

export function NewChatbotPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [source, setSource] = useState<"folder" | "uploads">("folder");
  const [folder, setFolder] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [preset, setPreset] = useState("balanced");
  const [models, setModels] = useState<ModelChoice>({});
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);

  const chosen: Config = {
    source: source === "uploads" ? { type: "uploads" } : { type: "local_folder", path: folder },
    ...(models.embedding && { embedding: models.embedding }),
    ...(models.generation && { generation: models.generation }),
  };

  const go = useMutation({
    mutationFn: async (then: "build" | "review") => {
      setProblems([]);
      let id = createdId;
      if (!id) {
        id = (await api.createKb(name.trim(), preset, chosen)).id;
        setCreatedId(id);
      } else {
        const current = (await api.getKb(id)).config;
        const merged = { ...current };
        for (const [stage, values] of Object.entries(chosen)) merged[stage] = { ...current[stage], ...values };
        await api.saveConfig(id, merged);
      }
      if (source === "uploads" && files.length) await api.upload(id, files);
      const config = (await api.getKb(id)).config;
      const check = await api.validate(id, config);
      // A missing answer model doesn't stop a build: Ask explains it, and answers start once the model is there.
      const blocking = check.errors.filter(
        (e) => e.loc[0] === "source" || (then === "build" && e.loc[0] !== "generation"),
      );
      if (blocking.length) {
        setProblems(blocking.map((e) => e.msg));
        setStep(blocking.some((e) => e.loc[0] === "source") ? 1 : 2);
        return null;
      }
      if (then === "review") return `/kb/${id}/manage/settings`;
      await api.startBuild(id);
      return `/kb/${id}/ask`;
    },
    onSuccess: (to) => to && navigate(to),
  });

  const valid = [name.trim().length > 0, source === "folder" ? folder.trim().length > 0 : files.length > 0, true];
  const last = step === STEPS.length - 1;

  return (
    <div className="flex min-h-full items-start justify-center bg-wall px-4 py-10 md:items-center">
      <form
        className="settle w-full max-w-xl rounded-[var(--radius-sheet)] bg-panel px-6 pt-8 pb-6 shadow-[0_0_0_0.5px_var(--color-rule),0_10px_40px_rgb(0_0_0/0.08)] md:px-10"
        onSubmit={(event) => {
          event.preventDefault();
          if (!valid[step]) return;
          if (last) go.mutate("build");
          else setStep(step + 1);
        }}
      >
        <ol className="flex justify-center gap-2" aria-label="Steps">
          {STEPS.map((label, index) => (
            <li
              key={label}
              aria-current={index === step ? "step" : undefined}
              className={cn(
                "h-1.5 rounded-full transition-all duration-300 ease-[var(--ease-out)]",
                index === step ? "w-6 bg-accent" : "w-1.5 bg-rule-strong",
              )}
            >
              <span className="sr-only">
                Step {index + 1} of {STEPS.length}: {label}
              </span>
            </li>
          ))}
        </ol>

        {step === 0 && (
          <div className="mt-8 text-center">
            <h1 className="font-display text-2xl font-bold tracking-tight">Name your chatbot</h1>
            <p className="mx-auto mt-2 max-w-sm text-base text-slate">
              Choose something people will recognise. You can change everything else later.
            </p>
            <label htmlFor="name" className="sr-only">
              Chatbot name
            </label>
            <Input
              id="name"
              autoFocus
              value={name}
              maxLength={80}
              placeholder="Team handbook"
              onChange={(e) => setName(e.target.value)}
              className="mx-auto mt-7 h-10 max-w-sm text-center text-lg"
            />
          </div>
        )}

        {step === 1 && (
          <div className="mt-8">
            <h1 className="text-center font-display text-2xl font-bold tracking-tight">Choose its documents</h1>
            <p className="mx-auto mt-2 max-w-sm text-center text-base text-slate">
              Documents, spreadsheets, slides, email, ebooks, images, recordings and videos all work.
            </p>
            <fieldset className="mt-7 divide-y divide-rule overflow-hidden rounded-[var(--radius-group)] shadow-[0_0_0_0.5px_var(--color-rule-strong)]">
              <legend className="sr-only">Where the documents are</legend>
              <Choice
                name="source"
                checked={source === "folder"}
                onSelect={() => setSource("folder")}
                icon={<FolderOpen />}
                title="A folder Archie can read"
                body="Documents on the computer or server running Archie."
              />
              <Choice
                name="source"
                checked={source === "uploads"}
                onSelect={() => setSource("uploads")}
                icon={<Upload />}
                title="Upload files"
                body="Pick files from this device. Good for a handful of documents."
              />
            </fieldset>
            {source === "folder" ? (
              <FolderPicker value={folder} onChange={setFolder} />
            ) : (
              <div className="mt-5">
                <label className="press inline-flex h-8 cursor-default items-center gap-2 rounded-[var(--radius-control)] bg-panel px-3.5 text-sm font-medium shadow-[0_0_0_0.5px_var(--color-rule-strong),0_1px_1px_rgb(0_0_0/0.06)] hover:bg-plinth">
                  <Upload className="size-4" aria-hidden /> Choose Files…
                  <input
                    type="file"
                    multiple
                    accept={ACCEPT}
                    className="sr-only"
                    onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                  />
                </label>
                {files.length > 0 && (
                  <ul className="mt-3 divide-y divide-rule rounded-[var(--radius-group)] bg-wall">
                    {files.map((file) => (
                      <li key={file.name} className="flex justify-between gap-3 px-3.5 py-2 text-sm">
                        <span className="truncate">{file.name}</span>
                        <span className="shrink-0 text-slate tabular-nums">{(file.size / 1024).toFixed(0)} KB</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="mt-8">
            <h1 className="text-center font-display text-2xl font-bold tracking-tight">How should it answer?</h1>
            <p className="mx-auto mt-2 max-w-sm text-center text-base text-slate">
              A starting point. Every setting can be tuned later in Manage.
            </p>
            <fieldset className="mt-7 divide-y divide-rule overflow-hidden rounded-[var(--radius-group)] shadow-[0_0_0_0.5px_var(--color-rule-strong)]">
              <legend className="sr-only">Answer style</legend>
              {PRESETS.map((item) => (
                <Choice
                  key={item.name}
                  name="preset"
                  checked={preset === item.name}
                  onSelect={() => setPreset(item.name)}
                  title={item.name === "balanced" ? `${item.title} (recommended)` : item.title}
                  body={item.body}
                />
              ))}
            </fieldset>
            <ModelCheck preset={preset} choice={models} onChoice={setModels} />
          </div>
        )}

        <div className="mt-6 space-y-3">
          {problems.length > 0 && (
            <div role="alert" className="rounded-[var(--radius-group)] bg-bad-soft px-4 py-3 text-sm text-bad">
              {problems.map((problem) => (
                <p key={problem}>{problem}</p>
              ))}
            </div>
          )}
          {go.error && <ErrorNote error={friendly(go.error)} />}
        </div>

        <div className="mt-8 flex items-center justify-between gap-3">
          <Button
            variant="ghost"
            onClick={() => (step === 0 ? navigate(-1) : setStep(step - 1))}
            disabled={go.isPending}
          >
            {step === 0 ? "Cancel" : "Back"}
          </Button>
          <div className="flex items-center gap-2">
            {last && (
              <Button disabled={go.isPending} onClick={() => go.mutate("review")}>
                Review Settings
              </Button>
            )}
            <Button type="submit" variant="primary" disabled={!valid[step]} busy={go.isPending} className="min-w-24">
              {last ? "Create" : "Continue"}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}
