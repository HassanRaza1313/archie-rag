import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, FileText, RotateCcw, ScanSearch, Search, Share, Square, UserRound } from "lucide-react";
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { TracePanel } from "../../components/TracePanel";
import {
  Badge,
  Button,
  buttonClass,
  cn,
  Dialog,
  EmptyState,
  ErrorNote,
  Group,
  Input,
  Skeleton,
  StatusBadge,
  TextArea,
} from "../../components/ui";
import { type AnswerSummary, api, type ChatEvent, chatBase, type Source, streamChat } from "../../lib/api";
import { formatMs, pages } from "../../lib/format";
import { can, useMe } from "../account/auth";
import { BuildProgress, useLiveJob } from "../build/BuildPage";
import { STAGE_LABELS, splitCitations } from "./citations";
import { ShareSheet } from "./ShareSheet";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources: Source[];
  citations: Source[];
  stage?: string;
  outcome?: string;
  verify?: string;
  verifyReason?: string;
  summary?: AnswerSummary;
  traceId?: string;
  error?: string;
  streaming?: boolean;
}

type Profile = { name: string; preferences: string };
type Conversation = { sessionId: string; messages: Message[] };
type Preview = { source: Source; from: { x: number; y: number } };

function readJson<T>(store: Storage, key: string, fallback: T): T {
  try {
    const raw = store.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(store: Storage, key: string, value: unknown): void {
  try {
    store.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or unavailable: the conversation still works for this page */
  }
}

/** The transcript and the server-side memory share one session id, so what you see is what Archie remembers. */
export function loadConversation(kbId: string): Conversation {
  const saved = readJson<Conversation | null>(sessionStorage, `archie.chat.${kbId}`, null);
  if (saved?.sessionId) {
    const settle = (m: Message): Message =>
      m.streaming && !m.text && !m.error
        ? { ...m, streaming: false, error: "the page closed before it finished" }
        : { ...m, streaming: false };
    return { ...saved, messages: saved.messages.map(settle) };
  }
  return { sessionId: crypto.randomUUID(), messages: [] };
}

/** The clicked element's centre in viewport pixels: where Quick Look grows from. */
export function centreOf(element: Element): { x: number; y: number } {
  const rect = element.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

/** transform-origin for a box at `box` so that scaling it grows from `point` (both in viewport pixels). */
export function originWithin(point: { x: number; y: number }, box: { left: number; top: number }): string {
  return `${Math.round(point.x - box.left)}px ${Math.round(point.y - box.top)}px`;
}

export type PassagePart = { kind: "text"; text: string } | { kind: "table"; rows: string[][] };

/** Split a passage into prose and markdown pipe tables, so tables read as tables in Quick Look. */
export function passageParts(text: string): PassagePart[] {
  const parts: PassagePart[] = [];
  let prose: string[] = [];
  let rows: string[][] = [];
  const flushProse = () => {
    if (prose.join("").trim()) parts.push({ kind: "text", text: prose.join("\n").trim() });
    prose = [];
  };
  const flushTable = () => {
    if (rows.length) parts.push({ kind: "table", rows });
    rows = [];
  };
  const lines = text.split("\n");
  for (const [index, raw] of lines.entries()) {
    let line = raw;
    // The chunker joins a table's header row onto the paragraph before it; split it back off.
    const glued = /^(.*?\S)\s+(\|(?:[^|]+\|){2,})\s*$/.exec(line);
    if (glued?.[1] && glued[2] && !line.trim().startsWith("|") && lines[index + 1]?.trim().startsWith("|")) {
      prose.push(glued[1]);
      line = glued[2];
    }
    const trimmed = line.trim();
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      flushProse();
      const cells = trimmed
        .slice(1, -1)
        .split("|")
        .map((cell) => cell.trim());
      if (!cells.every((cell) => /^:?-{3,}:?$/.test(cell))) rows.push(cells);
    } else {
      flushTable();
      prose.push(line);
    }
  }
  flushProse();
  flushTable();
  return parts;
}

function Passage({ text }: { text: string }) {
  return (
    <div className="space-y-3">
      {passageParts(text).map((part) =>
        part.kind === "text" ? (
          <p key={part.text} className="whitespace-pre-wrap">
            {part.text}
          </p>
        ) : (
          <div
            key={part.rows.map((row) => row.join("|")).join("\n")}
            className="overflow-x-auto rounded-[var(--radius-group)] shadow-[0_0_0_0.5px_var(--color-rule-strong)]"
          >
            <table className="w-full text-left text-sm">
              <thead className="bg-wall text-slate">
                <tr>
                  {part.rows[0]?.map((cell, column) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: columns are positional
                    <th key={column} className="px-3 py-1.5 font-medium">
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {part.rows.slice(1).map((row, rowIndex) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
                  <tr key={rowIndex}>
                    {row.map((cell, column) => (
                      // biome-ignore lint/suspicious/noArrayIndexKey: columns are positional
                      <td key={column} className="px-3 py-1.5 align-top">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ),
      )}
    </div>
  );
}

function verdictText(verify: string | undefined): { tone: "ok" | "warn"; text: string } | null {
  if (verify === "pass") return { tone: "ok", text: "Every claim cites a source" };
  if (verify === "fail") return { tone: "warn", text: "Some claims have no source" };
  return null;
}

function Answer({ message, onCite }: { message: Message; onCite: (label: string, from: Element) => void }) {
  const known = new Set(message.sources.map((source) => source.label));
  return (
    <div className={cn("text-[1.0625rem] leading-[1.6] whitespace-pre-wrap", message.streaming && "streaming-caret")}>
      {splitCitations(message.text).map((segment, index) =>
        segment.kind === "text" ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments derive from the text and never reorder
          <span key={index}>{segment.text}</span>
        ) : (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments derive from the text and never reorder
          <span key={index} className="mx-0.5 inline-flex gap-1 align-[2px]">
            {segment.labels.map((label) => (
              <button
                key={label}
                type="button"
                onClick={(event) => onCite(label, event.currentTarget)}
                disabled={!known.has(label)}
                aria-label={`Show source ${label}`}
                className="press h-5 cursor-default rounded-full bg-accent-soft px-2 text-xs leading-5 font-semibold text-accent hover:bg-accent hover:text-accent-ink disabled:bg-bad-soft disabled:text-bad"
              >
                {label}
              </button>
            ))}
          </span>
        ),
      )}
    </div>
  );
}

function Sources({ message, onOpen }: { message: Message; onOpen: (source: Source, from: Element) => void }) {
  const list = message.citations.length ? message.citations : message.sources;
  if (!list.length) return null;
  const closest = list.some((source) => source.closest);
  return (
    <section aria-label={closest ? "Closest passages" : "Sources"} className="mt-5">
      <h3 className="mb-1.5 text-sm font-semibold text-slate">{closest ? "Closest passages found" : "Sources"}</h3>
      <ul className="flex flex-wrap gap-2">
        {list.map((source) => (
          <li key={source.chunk_id} className="min-w-0 max-w-full sm:max-w-[48%]">
            <button
              type="button"
              onClick={(event) => onOpen(source, event.currentTarget)}
              className="press flex w-full cursor-default items-center gap-2.5 rounded-[var(--radius-group)] bg-panel py-2 pr-3.5 pl-2.5 text-left shadow-[0_0_0_0.5px_var(--color-rule-strong)] hover:bg-plinth"
            >
              <span className="grid size-8 shrink-0 place-items-center rounded-md bg-wall text-slate">
                <FileText className="size-4" aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="flex items-center gap-1.5 text-sm font-medium">
                  <span className="text-accent">{source.label}</span>
                  <span className="truncate">{source.file ?? "Document"}</span>
                </span>
                <span className="block truncate text-xs text-slate">
                  {[source.section, pages(source.page_start, source.page_end)].filter(Boolean).join(" · ")}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function HowAnswered({ message, onTrace }: { message: Message; onTrace?: () => void }) {
  const summary = message.summary;
  const verdict = verdictText(message.verify);
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-slate">
      {message.outcome && message.outcome !== "answered" && <StatusBadge status={message.outcome} />}
      {verdict && (
        <Badge tone={verdict.tone} title={message.verifyReason}>
          {verdict.text}
        </Badge>
      )}
      {summary && message.outcome !== "cache_hit" && (
        <span>
          Searched {summary.searched} passages, used {summary.used}
          {summary.confidence !== null && ` · best match ${Math.round(summary.confidence * 100)}%`}
          {summary.retries > 0 &&
            ` · reworded the search ${summary.retries === 1 ? "once" : `${summary.retries} times`}`}{" "}
          · {formatMs(summary.seconds * 1000)}
        </span>
      )}
      {message.traceId && onTrace && (
        <button
          type="button"
          onClick={onTrace}
          className="inline-flex cursor-default items-center gap-1 font-medium text-accent hover:underline"
        >
          <ScanSearch className="size-3.5" aria-hidden /> How Archie found this
        </button>
      )}
    </div>
  );
}

/** Quick Look for a source: the passage itself, growing from whatever opened it. Space or Escape closes it. */
function QuickLook({ preview, onClose }: { preview: Preview | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (preview && !dialog.open) {
      dialog.showModal();
      dialog.style.transformOrigin = originWithin(preview.from, dialog.getBoundingClientRect());
    }
    if (!preview && dialog.open) dialog.close();
  }, [preview]);
  const source = preview?.source;
  return (
    <dialog
      ref={ref}
      aria-label={source ? `Source ${source.label}: ${source.file ?? "Document"}` : "Source"}
      onClose={onClose}
      onClick={(event) => event.target === ref.current && onClose()}
      onKeyDown={(event) => {
        if (event.key === " ") {
          event.preventDefault();
          onClose();
        }
      }}
      className="quick-look m-auto w-[calc(100%-2rem)] max-w-xl rounded-[var(--radius-sheet)] bg-panel p-0 text-ink shadow-[0_0_0_0.5px_rgb(0_0_0/0.12),0_30px_80px_rgb(0_0_0/0.3)]"
    >
      {source && (
        <div>
          <div className="flex items-center gap-3 border-b border-rule px-5 py-3.5">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-wall text-slate">
              <FileText className="size-[18px]" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-base font-semibold">{source.file ?? "Document"}</div>
              <div className="truncate text-sm text-slate">
                {[source.section, pages(source.page_start, source.page_end)].filter(Boolean).join(" · ")}
              </div>
            </div>
            <Badge tone="accent">
              {source.label}
              {!source.closest && ` · ${Math.round(source.relevance * 100)}% match`}
            </Badge>
          </div>
          <div className="max-h-[55vh] overflow-y-auto px-5 py-4 text-base leading-relaxed">
            <Passage text={source.text ?? source.preview} />
          </div>
          <div className="flex justify-between gap-3 border-t border-rule px-5 py-3 text-xs text-slate">
            <span>Press Space or Esc to close</span>
            <button type="button" onClick={onClose} className="cursor-default font-medium text-accent hover:underline">
              Done
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}

function ProfileDialog({
  profile,
  onSave,
  onClose,
}: {
  profile: Profile;
  onSave: (p: Profile) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(profile);
  return (
    <Dialog
      open
      onClose={onClose}
      title="About You"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              onSave(draft);
              onClose();
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <p className="text-sm text-slate">
        Archie uses this to adjust its tone, never its facts. Personal answers are not reused for others. Stored in this
        browser only.
      </p>
      <Group className="mt-4">
        <label htmlFor="profile-name" className="flex items-center gap-4 px-4 py-2">
          <span className="w-36 shrink-0 text-base">Your name</span>
          <Input
            id="profile-name"
            data-autofocus
            value={draft.name}
            maxLength={100}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <label htmlFor="profile-preferences" className="flex items-center gap-4 px-4 py-2">
          <span className="w-36 shrink-0 text-base">How you like answers</span>
          <Input
            id="profile-preferences"
            value={draft.preferences}
            maxLength={300}
            placeholder="Short, with bullet points"
            onChange={(e) => setDraft({ ...draft, preferences: e.target.value })}
          />
        </label>
      </Group>
    </Dialog>
  );
}

/** Before the first build finishes: its progress, or one button to start it. */
function NotReady({ kbId, name, jobId }: { kbId: string; name: string; jobId: string | null }) {
  const queryClient = useQueryClient();
  const { job } = useLiveJob(jobId);
  const start = useMutation({
    mutationFn: () => api.startBuild(kbId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["kb", kbId] }),
  });
  return (
    <div className="settle mx-auto max-w-md py-16 text-center">
      <h2 className="font-display text-xl font-semibold tracking-tight">
        {job ? `Archie is reading ${name}'s documents` : `${name} hasn't read its documents yet`}
      </h2>
      <p className="mt-2 text-base text-slate">
        {job
          ? "This usually takes a few minutes. You can ask as soon as it's done."
          : "Build it once and you can start asking."}
      </p>
      {job ? (
        <div className="mt-7 rounded-[var(--radius-group)] bg-panel px-5 py-4 text-left shadow-[0_0_0_0.5px_var(--color-rule)]">
          <BuildProgress job={job} />
        </div>
      ) : (
        <div className="mt-6 flex justify-center gap-2">
          <Button variant="primary" busy={start.isPending} onClick={() => start.mutate()}>
            Build Now
          </Button>
          <Link to={`/kb/${kbId}/manage/settings`} className={buttonClass("secondary")}>
            Review Settings
          </Link>
        </div>
      )}
      <div className="mt-4">
        <ErrorNote error={start.error} />
      </div>
    </div>
  );
}

/** One conversation with a chatbot. Ask (for the owner) and shared links (for anyone) both use it. */
export function ChatView({
  base,
  storageKey,
  name,
  documents,
  built,
  actions,
  notReady,
  canTrace = false,
}: {
  base: string;
  storageKey: string;
  name: string;
  documents: number;
  built: boolean;
  actions?: ReactNode;
  notReady?: ReactNode;
  canTrace?: boolean;
}) {
  const suggestions = useQuery({
    queryKey: ["suggestions", base],
    queryFn: () => api.suggestions(base),
    enabled: built,
  });
  const [conversation, setConversation] = useState<Conversation>(() => loadConversation(storageKey));
  const [input, setInput] = useState("");
  const [profile, setProfile] = useState<Profile>(() =>
    readJson(localStorage, "archie.profile", { name: "", preferences: "" }),
  );
  const [profileOpen, setProfileOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const messages = conversation.messages;
  const streaming = messages.some((message) => message.streaming);

  useEffect(() => {
    writeJson(sessionStorage, `archie.chat.${storageKey}`, conversation);
  }, [conversation, storageKey]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: follow the newest token as it streams
  useEffect(() => {
    // Block body: newer browsers return a Promise here, which React would call as a cleanup.
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages]);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(target.tagName)) {
        event.preventDefault();
        field.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const update = (id: string, change: (message: Message) => Message) =>
    setConversation((current) => ({
      ...current,
      messages: current.messages.map((message) => (message.id === id ? change(message) : message)),
    }));

  async function send(query: string) {
    const assistantId = crypto.randomUUID();
    setConversation((current) => ({
      ...current,
      messages: [
        ...current.messages,
        { id: crypto.randomUUID(), role: "user", text: query, sources: [], citations: [] },
        { id: assistantId, role: "assistant", text: "", sources: [], citations: [], streaming: true },
      ],
    }));
    const controller = new AbortController();
    abort.current = controller;
    let finalText = "";
    const apply = (event: ChatEvent) =>
      update(assistantId, (message) => {
        switch (event.type) {
          case "status":
            return { ...message, stage: event.stage };
          case "sources":
            return { ...message, sources: event.sources };
          case "token":
            finalText += event.text;
            return { ...message, text: message.text + event.text };
          case "verdict":
            return { ...message, verify: event.verdict, verifyReason: event.reason };
          case "done":
            return {
              ...message,
              outcome: event.outcome,
              citations: event.citations,
              summary: event.summary,
              verify: event.verify ?? message.verify,
              verifyReason: event.verify_reason ?? message.verifyReason,
              traceId: event.trace_id,
              streaming: false,
            };
          case "error":
            return { ...message, error: event.message, traceId: event.trace_id, streaming: false };
        }
      });
    try {
      const body = {
        query,
        session_id: conversation.sessionId,
        profile: Object.fromEntries(Object.entries(profile).filter(([, value]) => value.trim())),
      };
      for await (const event of await streamChat(base, body, controller.signal)) apply(event);
      setAnnouncement(`Archie answered: ${finalText.replace(/\[S\d+[^\]]*\]/g, "")}`);
    } catch (error) {
      if (!controller.signal.aborted) {
        update(assistantId, (message) => ({ ...message, error: (error as Error).message, streaming: false }));
      }
    } finally {
      update(assistantId, (message) => ({ ...message, streaming: false }));
    }
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const query = input.trim();
    if (!query || streaming) return;
    setInput("");
    void send(query);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  async function newConversation() {
    abort.current?.abort();
    await api.clearSession(base, conversation.sessionId).catch(() => undefined);
    setConversation({ sessionId: crypto.randomUUID(), messages: [] });
  }

  const open = (source: Source | undefined, from: Element) => source && setPreview({ source, from: centreOf(from) });

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[46rem] px-5 pt-7 pb-10 md:px-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h1 className="font-display text-2xl font-bold tracking-[-0.015em]">Ask {name}</h1>
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" onClick={() => setProfileOpen(true)}>
                <UserRound className="size-3.5" aria-hidden /> {profile.name || "About You"}
              </Button>
              <Button size="sm" variant="ghost" onClick={newConversation} disabled={!messages.length}>
                <RotateCcw className="size-3.5" aria-hidden /> New Conversation
              </Button>
              {actions}
            </div>
          </div>

          {!built && notReady}

          {built && messages.length === 0 && (
            <div className="settle pt-10">
              <p className="text-lg text-slate">
                Archie answers from {documents} document{documents === 1 ? "" : "s"} and shows where every answer came
                from.
              </p>
              {(suggestions.data?.length ?? 0) > 0 && (
                <Group title="Try asking" className="mt-7">
                  {suggestions.data?.map((question) => (
                    <button
                      key={question}
                      type="button"
                      onClick={() => void send(question)}
                      className="flex w-full cursor-default items-center gap-3 px-4 py-3 text-left text-base hover:bg-plinth"
                    >
                      <Search className="size-4 shrink-0 text-faint" aria-hidden />
                      {question}
                    </button>
                  ))}
                </Group>
              )}
            </div>
          )}

          <ol className="mt-6 space-y-9">
            {messages.map((message) =>
              message.role === "user" ? (
                <li key={message.id} className="flex justify-end">
                  <div className="max-w-[85%] rounded-[18px] rounded-br-md bg-bubble px-4 py-2.5 text-base whitespace-pre-wrap text-bubble-ink">
                    {message.text}
                  </div>
                </li>
              ) : (
                <li key={message.id} className="settle">
                  {message.streaming && !message.text && (
                    <p className="flex items-center gap-2 text-base text-slate">
                      <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden />
                      {STAGE_LABELS[message.stage ?? ""] ?? "Reading your question"}…
                    </p>
                  )}
                  {message.error ? (
                    <p role="alert" className="text-base text-bad">
                      Archie couldn't answer: {message.error}
                    </p>
                  ) : (
                    <Answer
                      message={message}
                      onCite={(label, from) =>
                        open(
                          message.sources.find((s) => s.label === label),
                          from,
                        )
                      }
                    />
                  )}
                  {!message.streaming && (
                    <>
                      <Sources message={message} onOpen={(source, from) => open(source, from)} />
                      <HowAnswered
                        message={message}
                        onTrace={canTrace ? () => setTraceId(message.traceId ?? null) : undefined}
                      />
                    </>
                  )}
                </li>
              ),
            )}
          </ol>
          <div ref={bottom} />
          <div className="sr-only" aria-live="polite">
            {announcement}
          </div>
        </div>
      </div>

      <form onSubmit={submit} className="px-4 pt-2 pb-4 md:px-8">
        <div className="mx-auto flex max-w-[46rem] items-end gap-2 rounded-[14px] bg-panel py-1.5 pr-1.5 pl-4 shadow-[0_0_0_0.5px_var(--color-rule-strong),0_4px_16px_rgb(0_0_0/0.06)] focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_30%,transparent),0_4px_16px_rgb(0_0_0/0.06)]">
          <TextArea
            ref={field}
            rows={1}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
            }}
            onKeyDown={onKeyDown}
            placeholder={built ? `Ask ${name} anything…` : "Available once the first build finishes"}
            disabled={!built}
            maxLength={4000}
            aria-label="Your question"
            className="min-h-9 resize-none bg-transparent px-0 py-1.5 text-[1.0625rem] shadow-none focus:shadow-none"
          />
          {streaming ? (
            <button
              type="button"
              aria-label="Stop answering"
              onClick={() => abort.current?.abort()}
              className="press grid size-8 shrink-0 cursor-default place-items-center rounded-full bg-ink text-panel"
            >
              <Square className="size-3 fill-current" />
            </button>
          ) : (
            <button
              type="submit"
              aria-label="Ask"
              disabled={!input.trim() || !built}
              className="press grid size-8 shrink-0 cursor-default place-items-center rounded-full bg-accent text-accent-ink disabled:bg-plinth disabled:text-faint"
            >
              <ArrowUp className="size-4" strokeWidth={2.5} />
            </button>
          )}
        </div>
        <p className="mx-auto mt-1.5 hidden max-w-[46rem] text-center text-xs text-slate md:block">
          Return to ask · Shift-Return for a new line · / to type a question
        </p>
      </form>

      <QuickLook preview={preview} onClose={() => setPreview(null)} />
      {profileOpen && (
        <ProfileDialog
          profile={profile}
          onClose={() => setProfileOpen(false)}
          onSave={(value) => {
            setProfile(value);
            writeJson(localStorage, "archie.profile", value);
          }}
        />
      )}
      <Dialog open={!!traceId} onClose={() => setTraceId(null)} title="How Archie Found This" variant="side">
        {traceId && <TracePanel traceId={traceId} />}
      </Dialog>
    </div>
  );
}

export function ChatPage() {
  const { kbId = "" } = useParams();
  const [sharing, setSharing] = useState(false);
  const me = useMe();
  const editor = can(me.data, "editor");
  const kb = useQuery({
    queryKey: ["kb", kbId],
    queryFn: () => api.getKb(kbId),
    refetchInterval: (query) => (query.state.data?.active_job ? 3_000 : false),
  });
  if (kb.isLoading || !kb.data) {
    return (
      <div className="px-8 py-10">
        <Skeleton />
      </div>
    );
  }
  const data = kb.data;
  return (
    <>
      <ChatView
        key={kbId}
        base={chatBase(kbId)}
        storageKey={kbId}
        name={data.name}
        documents={data.documents}
        built={Boolean(data.built_version)}
        canTrace
        notReady={
          editor ? (
            <NotReady kbId={kbId} name={data.name} jobId={data.active_job?.id ?? null} />
          ) : (
            <EmptyState title={`${data.name} isn't ready yet`}>Ask an editor to build it.</EmptyState>
          )
        }
        actions={
          <>
            {data.status === "stale" && <StatusBadge status="stale" />}
            {editor && (
              <Button size="sm" variant="ghost" onClick={() => setSharing(true)}>
                <Share className="size-3.5" aria-hidden /> {data.share_token ? "Shared" : "Share"}
              </Button>
            )}
          </>
        }
      />
      {sharing && <ShareSheet kb={data} onClose={() => setSharing(false)} />}
    </>
  );
}
