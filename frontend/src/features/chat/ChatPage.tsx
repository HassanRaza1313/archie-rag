import { useQuery } from "@tanstack/react-query";
import { ArrowUp, RotateCcw, ScanSearch, Square, UserRound } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { TracePanel } from "../../components/TracePanel";
import {
  Badge,
  Button,
  buttonClass,
  cn,
  Dialog,
  GalleryLabel,
  Input,
  Skeleton,
  StatusBadge,
  TextArea,
} from "../../components/ui";
import { type AnswerSummary, api, type ChatEvent, type Source, streamChat } from "../../lib/api";
import { formatMs, pages } from "../../lib/format";
import { STAGE_LABELS, splitCitations } from "./citations";

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

function verdictText(verify: string | undefined): { tone: "ok" | "warn"; text: string } | null {
  if (verify === "pass") return { tone: "ok", text: "Every claim cites a source" };
  if (verify === "fail") return { tone: "warn", text: "Some claims have no source" };
  return null;
}

function Answer({ message, onCite }: { message: Message; onCite: (label: string) => void }) {
  const known = new Set(message.sources.map((source) => source.label));
  return (
    <div className={cn("text-base leading-relaxed whitespace-pre-wrap", message.streaming && "streaming-caret")}>
      {splitCitations(message.text).map((segment, index) =>
        segment.kind === "text" ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments derive from the text and never reorder
          <span key={index}>{segment.text}</span>
        ) : (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments derive from the text and never reorder
          <span key={index} className="mx-0.5 inline-flex gap-1 align-[1px]">
            {segment.labels.map((label) => (
              <button
                key={label}
                type="button"
                onClick={() => onCite(label)}
                disabled={!known.has(label)}
                aria-label={`Source ${label}`}
                className="rounded-[3px] bg-accent-soft px-1.5 font-mono text-xs font-semibold text-accent hover:bg-accent hover:text-accent-ink disabled:bg-bad-soft disabled:text-bad"
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

function SourceLabels({
  message,
  highlighted,
  onOpen,
}: {
  message: Message;
  highlighted: string | null;
  onOpen: (source: Source) => void;
}) {
  const list = message.citations.length ? message.citations : message.sources;
  if (!list.length) return null;
  const closest = list.some((source) => source.closest);
  return (
    <aside aria-label={closest ? "Closest passages" : "Sources"} className="space-y-2">
      <div className="text-xs font-semibold text-slate">{closest ? "Closest passages found" : "Sources"}</div>
      {list.map((source) => (
        <button
          key={source.chunk_id}
          type="button"
          onClick={() => onOpen(source)}
          className={cn(
            "block w-full text-left transition-shadow duration-200",
            highlighted === source.label && "rounded-[3px] ring-2 ring-accent",
          )}
        >
          <GalleryLabel
            mark={`${source.label}${source.closest ? "" : ` · ${Math.round(source.relevance * 100)}% match`}`}
            title={source.file ?? "Document"}
            className="hover:border-rule-strong"
          >
            {[source.section, pages(source.page_start, source.page_end)].filter(Boolean).join(" · ")}
          </GalleryLabel>
        </button>
      ))}
    </aside>
  );
}

function HowAnswered({ message, onTrace }: { message: Message; onTrace: () => void }) {
  const summary = message.summary;
  const verdict = verdictText(message.verify);
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-slate">
      {message.outcome && message.outcome !== "answered" && <StatusBadge status={message.outcome} />}
      {verdict && (
        <Badge tone={verdict.tone} title={message.verifyReason}>
          {verdict.text}
        </Badge>
      )}
      {summary && message.outcome !== "cache_hit" && (
        <span>
          Searched {summary.searched} passages · used {summary.used}
          {summary.confidence !== null && ` · ${Math.round(summary.confidence * 100)}% confident`}
          {summary.retries > 0 &&
            ` · reworded the search ${summary.retries === 1 ? "once" : `${summary.retries} times`}`}{" "}
          · {formatMs(summary.seconds * 1000)}
        </span>
      )}
      {message.traceId && (
        <button
          type="button"
          onClick={onTrace}
          className="inline-flex items-center gap-1 font-semibold text-accent underline-offset-4 hover:underline"
        >
          <ScanSearch className="size-3.5" aria-hidden /> How Archie found this
        </button>
      )}
    </div>
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
      title="About you"
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
      <div className="mt-4 space-y-4">
        <label htmlFor="profile-name" className="block space-y-1.5">
          <span className="font-semibold">Your name</span>
          <Input
            id="profile-name"
            data-autofocus
            value={draft.name}
            maxLength={100}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <label htmlFor="profile-preferences" className="block space-y-1.5">
          <span className="font-semibold">How you like answers</span>
          <Input
            id="profile-preferences"
            value={draft.preferences}
            maxLength={300}
            placeholder="Short, with bullet points"
            onChange={(e) => setDraft({ ...draft, preferences: e.target.value })}
          />
        </label>
      </div>
    </Dialog>
  );
}

export function ChatPage() {
  const { kbId = "" } = useParams();
  const kb = useQuery({ queryKey: ["kb", kbId], queryFn: () => api.getKb(kbId) });
  const built = Boolean(kb.data?.built_version);
  const suggestions = useQuery({
    queryKey: ["suggestions", kbId],
    queryFn: () => api.suggestions(kbId),
    enabled: built,
  });
  const [conversation, setConversation] = useState<Conversation>(() => loadConversation(kbId));
  const [input, setInput] = useState("");
  const [profile, setProfile] = useState<Profile>(() =>
    readJson(localStorage, "archie.profile", { name: "", preferences: "" }),
  );
  const [profileOpen, setProfileOpen] = useState(false);
  const [preview, setPreview] = useState<Source | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<{ message: string; label: string } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const messages = conversation.messages;
  const streaming = messages.some((message) => message.streaming);

  useEffect(() => {
    writeJson(sessionStorage, `archie.chat.${kbId}`, conversation);
  }, [conversation, kbId]);
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
      for await (const event of await streamChat(kbId, body, controller.signal)) apply(event);
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
    await api.clearSession(kbId, conversation.sessionId).catch(() => undefined);
    setConversation({ sessionId: crypto.randomUUID(), messages: [] });
  }

  if (kb.isLoading) {
    return (
      <div className="px-5 py-10 md:px-10">
        <Skeleton />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule bg-panel px-5 py-3 md:px-10">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="truncate text-base font-semibold">Ask {kb.data?.name}</h1>
          {kb.data?.status !== "ready" && <StatusBadge status={kb.data?.status} />}
        </div>
        <div className="-ml-3 flex items-center gap-1 sm:ml-0">
          <Button size="sm" variant="ghost" onClick={() => setProfileOpen(true)}>
            <UserRound className="size-4" aria-hidden /> {profile.name || "About you"}
          </Button>
          <Button size="sm" variant="ghost" onClick={newConversation} disabled={!messages.length}>
            <RotateCcw className="size-4" aria-hidden /> New conversation
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-5 py-8 md:px-10">
          {!built && (
            <div className="max-w-2xl py-10">
              <h2 className="text-lg font-semibold">This chatbot hasn't read its documents yet</h2>
              <p className="mt-2 text-sm text-slate">
                Build it once and you can start asking. It usually takes a few minutes.
              </p>
              <Link to={`/kb/${kbId}/setup`} className={cn(buttonClass("primary"), "mt-5")}>
                Go to setup
              </Link>
            </div>
          )}
          {built && messages.length === 0 && (
            <div className="max-w-2xl py-10">
              <h2 className="text-xl font-semibold tracking-tight">What would you like to know?</h2>
              <p className="mt-2 text-sm text-slate">
                Archie answers from {kb.data?.documents} document{kb.data?.documents === 1 ? "" : "s"} and labels every
                answer with its sources.
              </p>
              {(suggestions.data?.length ?? 0) > 0 && (
                <div className="mt-6 space-y-2">
                  <div className="text-xs font-semibold text-slate">Try asking</div>
                  {suggestions.data?.map((question) => (
                    <button
                      key={question}
                      type="button"
                      onClick={() => void send(question)}
                      className="block w-full rounded-lg border border-rule bg-panel px-4 py-3 text-left text-sm hover:border-accent hover:text-accent"
                    >
                      {question}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <ol className="space-y-10">
            {messages.map((message) =>
              message.role === "user" ? (
                <li key={message.id} className="flex justify-end">
                  <div className="max-w-[80%] rounded-xl bg-plinth px-4 py-2.5 text-base whitespace-pre-wrap">
                    {message.text}
                  </div>
                </li>
              ) : (
                <li key={message.id} className="settle grid gap-6 lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-10">
                  <div className="min-w-0">
                    {message.streaming && !message.text && (
                      <p className="flex items-center gap-2 text-sm text-slate">
                        <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden />
                        {STAGE_LABELS[message.stage ?? ""] ?? "Reading your question"}…
                      </p>
                    )}
                    {message.error ? (
                      <p role="alert" className="text-base text-bad">
                        Archie couldn't answer: {message.error}
                      </p>
                    ) : (
                      <Answer message={message} onCite={(label) => setHighlight({ message: message.id, label })} />
                    )}
                    {!message.streaming && (
                      <HowAnswered message={message} onTrace={() => message.traceId && setTraceId(message.traceId)} />
                    )}
                  </div>
                  {!message.streaming && (
                    <SourceLabels
                      message={message}
                      highlighted={highlight?.message === message.id ? highlight.label : null}
                      onOpen={setPreview}
                    />
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

      <form onSubmit={submit} className="border-t border-rule bg-panel px-5 py-4 md:px-10">
        <div className="mx-auto flex max-w-6xl items-end gap-2">
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
            placeholder={built ? `Ask ${kb.data?.name ?? "Archie"} anything…` : "Build this chatbot to start asking"}
            disabled={!built}
            maxLength={4000}
            aria-label="Your question"
            className="min-h-12 resize-none text-base"
          />
          {streaming ? (
            <Button aria-label="Stop answering" size="lg" onClick={() => abort.current?.abort()}>
              <Square className="size-4" />
            </Button>
          ) : (
            <Button type="submit" variant="primary" size="lg" aria-label="Ask" disabled={!input.trim() || !built}>
              <ArrowUp className="size-5" />
            </Button>
          )}
        </div>
        <p className="mx-auto mt-2 hidden max-w-6xl text-xs text-slate md:block">
          Enter to ask · Shift + Enter for a new line · / to jump here
        </p>
      </form>

      <Dialog open={!!preview} onClose={() => setPreview(null)} title={preview?.file ?? "Source"}>
        {preview && (
          <div className="space-y-3 text-sm">
            <GalleryLabel mark={preview.label} title={preview.file ?? "Document"}>
              {[
                preview.section,
                pages(preview.page_start, preview.page_end),
                `${Math.round(preview.relevance * 100)}% match`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </GalleryLabel>
            <p className="leading-relaxed whitespace-pre-wrap">
              {preview.preview}
              {preview.preview.length >= 400 && "…"}
            </p>
          </div>
        )}
      </Dialog>
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
      <Dialog open={!!traceId} onClose={() => setTraceId(null)} title="How Archie found this" variant="side">
        {traceId && <TracePanel traceId={traceId} />}
      </Dialog>
    </div>
  );
}
