import { useMutation, useQuery } from "@tanstack/react-query";
import { FolderOpen, Upload } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button, cn, ErrorNote, GalleryLabel, Input } from "../../components/ui";
import { ApiError, api, type Config } from "../../lib/api";

const PRESETS: { name: string; title: string; body: string }[] = [
  { name: "balanced", title: "Balanced", body: "Good answers at a comfortable speed. Best for most collections." },
  { name: "high_accuracy", title: "Most careful", body: "Checks more passages and double-checks answers. Slower." },
  { name: "low_latency", title: "Fastest", body: "Answers quickly from fewer passages. Good for simple questions." },
];

function Step({ index, title, hint, children }: { index: number; title: string; hint: string; children: ReactNode }) {
  return (
    <section className="grid gap-4 border-t border-rule py-8 md:grid-cols-[16rem_minmax(0,1fr)] md:gap-10">
      <div>
        <h2 className="text-lg font-semibold">
          {index}. {title}
        </h2>
        <p className="mt-1 text-sm text-slate">{hint}</p>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

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
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3.5 transition-colors duration-150",
        checked ? "border-accent bg-accent-soft" : "border-rule-strong bg-panel hover:border-slate",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onSelect}
        className="mt-1 accent-[var(--color-accent)]"
      />
      {icon && <span className="mt-0.5 text-slate [&>svg]:size-5">{icon}</span>}
      <span>
        <span className="block font-semibold">{title}</span>
        <span className="block text-sm text-slate">{body}</span>
      </span>
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
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities, staleTime: 60_000 });
  const [name, setName] = useState("");
  const [source, setSource] = useState<"folder" | "uploads">("folder");
  const [folder, setFolder] = useState("data/sources/sample");
  const [files, setFiles] = useState<File[]>([]);
  const [preset, setPreset] = useState("balanced");
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);

  const sourceConfig: Config = {
    source: source === "uploads" ? { type: "uploads" } : { type: "local_folder", path: folder },
  };

  const go = useMutation({
    mutationFn: async (then: "build" | "review") => {
      setProblems([]);
      let id = createdId;
      if (!id) {
        id = (await api.createKb(name.trim(), preset, sourceConfig)).id;
        setCreatedId(id);
      } else {
        const current = (await api.getKb(id)).config;
        await api.saveConfig(id, {
          ...current,
          ...sourceConfig,
          source: { ...current.source, ...sourceConfig.source },
        });
      }
      if (source === "uploads" && files.length) await api.upload(id, files);
      const config = (await api.getKb(id)).config;
      const check = await api.validate(id, config);
      const blocking = check.errors.filter((e) => e.loc[0] === "source" || then === "build");
      if (blocking.length) {
        setProblems(blocking.map((e) => e.msg));
        return null;
      }
      if (then === "review") return `/kb/${id}/setup`;
      const { job_id } = await api.startBuild(id);
      return `/kb/${id}/builds/${job_id}`;
    },
    onSuccess: (to) => to && navigate(to),
  });

  const ready = name.trim().length > 0 && (source === "folder" ? folder.trim().length > 0 : files.length > 0);
  const roots = capabilities.data?.data_roots ?? [];

  return (
    <div className="px-5 pt-8 pb-16 md:px-10 md:pt-10">
      <Link to="/" className="text-sm text-slate hover:text-ink">
        ← Your chatbots
      </Link>
      <h1 className="mt-4 text-xl font-semibold tracking-tight">Create a chatbot</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate">
        Three choices, then Archie reads your documents. You can change everything later in Setup.
      </p>

      <form
        className="mt-6 max-w-5xl"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) go.mutate("build");
        }}
      >
        <Step index={1} title="Name it" hint="Use a name people will recognise.">
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
            className="max-w-md"
          />
        </Step>

        <Step index={2} title="Choose documents" hint="PDF, Markdown and text files work. Scanned PDFs need OCR.">
          <fieldset className="grid gap-3 sm:grid-cols-2">
            <legend className="sr-only">Where the documents are</legend>
            <Choice
              name="source"
              checked={source === "folder"}
              onSelect={() => setSource("folder")}
              icon={<FolderOpen />}
              title="A folder Archie can read"
              body="Documents already on the computer or server running Archie."
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
            <div className="mt-4 max-w-xl">
              <label htmlFor="folder" className="text-sm font-semibold">
                Folder
              </label>
              <Input
                id="folder"
                className="mt-1.5 font-mono"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
              />
              <p className="mt-1.5 text-xs text-slate">
                {roots.length
                  ? `Archie may read folders inside: ${roots.join(", ")}.`
                  : "Archie may only read folders its administrator has allowed."}
              </p>
            </div>
          ) : (
            <div className="mt-4">
              <label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-lg border border-rule-strong bg-panel px-4 text-sm font-semibold hover:border-ink">
                <Upload className="size-4" aria-hidden /> Choose files
                <input
                  type="file"
                  multiple
                  accept=".pdf,.md,.txt"
                  className="sr-only"
                  onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                />
              </label>
              {files.length > 0 && (
                <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                  {files.map((file) => (
                    <GalleryLabel as="li" key={file.name} title={file.name}>
                      {(file.size / 1024).toFixed(0)} KB
                    </GalleryLabel>
                  ))}
                </ul>
              )}
            </div>
          )}
        </Step>

        <Step index={3} title="How should it answer?" hint="A starting point. Every setting can be tuned later.">
          <fieldset className="grid gap-3">
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
        </Step>

        <div className="flex flex-col gap-4 border-t border-rule pt-8 md:pl-[calc(16rem+2.5rem)]">
          {problems.length > 0 && (
            <div role="alert" className="rounded-lg bg-bad-soft px-4 py-3 text-sm text-bad">
              {problems.map((problem) => (
                <p key={problem}>{problem}</p>
              ))}
            </div>
          )}
          {go.error && <ErrorNote error={friendly(go.error)} />}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" size="lg" disabled={!ready} busy={go.isPending}>
              Create and build
            </Button>
            <Button size="lg" disabled={!ready || go.isPending} onClick={() => go.mutate("review")}>
              Review all settings first
            </Button>
          </div>
          <p className="text-xs text-slate">
            Building usually takes a few minutes. Archie shows its progress as it reads.
          </p>
        </div>
      </form>
    </div>
  );
}
