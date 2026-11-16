/** Typed client for the ragkit API. Requests share one origin with the UI (Vite proxy in dev, same server in prod). */

export type KbStatus = "draft" | "building" | "ready" | "stale" | "failed";
export type JobStatus = "queued" | "running" | "completed" | "completed_with_errors" | "failed" | "cancelled";
export type Config = Record<string, Record<string, unknown>>;

export interface JobRef {
  id: string;
  status: JobStatus;
  config_version?: number;
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
  error?: string | null;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  status: KbStatus;
  created_at: number;
  active_version: number;
  built_version: number | null;
  documents: number;
  chunks: number;
  active_job: JobRef | null;
  last_job: JobRef | null;
  /** Anyone with /share/<token> may ask this chatbot questions; null when it is not shared. */
  share_token: string | null;
}

export interface KnowledgeBaseDetail extends KnowledgeBase {
  config: Config;
}

export interface JobStage {
  stage: string;
  position: number;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  items_in: number;
  items_out: number;
  items_skipped: number;
  items_failed: number;
  started_at: number | null;
  finished_at: number | null;
  detail: Record<string, unknown> | null;
}

export interface Job extends JobRef {
  kb_id: string;
  config_version: number;
  summary: Record<string, number | string | null> | null;
  stages: JobStage[];
  failures: { document: string; stage: string; error: string }[];
}

export interface Source {
  label: string;
  chunk_id: string;
  file: string | null;
  /** The chatbot this passage came from, when the answer searched several. */
  group?: string | null;
  document_uri?: string | null;
  section: string | null;
  page_start: number | null;
  page_end: number | null;
  relevance: number;
  preview: string;
  text?: string;
  closest?: boolean;
}

export type ChatEvent =
  | { type: "status"; stage: string }
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "verdict"; verdict: string; reason: string }
  | {
      type: "done";
      outcome: string;
      citations: Source[];
      verify?: string;
      verify_reason?: string;
      claims?: Claim[];
      summary?: AnswerSummary;
      trace_id: string;
    }
  | { type: "error"; message: string; trace_id?: string };

/** An account a connector reads with (S3, a database, Atlassian, Microsoft 365, Google Drive, Notion). */
export interface SourceAccount {
  id: string;
  kind: string;
  name: string;
  settings: Record<string, string>;
  hint: string | null;
  created_at: number;
}

export interface SourceCatalog {
  accounts: SourceAccount[];
  /** Account kinds: their label and form fields as [key, label] pairs. */
  kinds: Record<string, { label: string; settings: [string, string][]; secrets: [string, string][] }>;
  /** Source types read through an account: which kind they need and what their location is. */
  sources: Record<string, { account: string; location: string }>;
}

/** A question people keep asking that went unanswered or was rated unhelpful. */
export interface Gap {
  kb_id: string;
  question: string;
  asked: number;
  unanswered: number;
  unhelpful: number;
  last_asked: number;
}

/** One sentence of an answer, checked against the sources it cites. */
export interface Claim {
  text: string;
  support: number;
  backed: boolean;
  sources: string[];
}

export interface AnswerSummary {
  searched: number;
  used: number;
  confidence: number | null;
  retries: number;
  seconds: number;
}

export interface Slo {
  name: string;
  description: string;
  target: number;
  window_days: number;
  requests: number;
  sli: number | null;
  error_budget_remaining: number | null;
  met: boolean | null;
}

export interface Alert {
  name: string;
  slo: string | null;
  severity: "page" | "ticket";
  state: "firing" | "ok";
  burn_rate?: number;
  threshold?: number;
  window?: string;
  requests?: number;
  kb_id?: string;
  job_id?: string;
  detail?: string | null;
}

export interface Span {
  name: string;
  start_ms: number;
  duration_ms: number;
  attributes: Record<string, unknown>;
  error?: string;
}

export interface TraceSummary {
  id: string;
  kb_id: string;
  config_version: number;
  session_id: string | null;
  created_at: number;
  outcome: string;
  total_ms: number;
  ttft_ms: number | null;
  cache_hit: number;
  confidence: number | null;
  retries: number;
  verify: string | null;
  query: string;
}

export interface Candidate {
  rank: number;
  chunk_id: string;
  file: string | null;
  section: string | null;
  page: number | null;
  dense_rank: number | null;
  keyword_rank: number | null;
  fused: number;
  relevance: number;
  text?: string;
}

export interface Trace extends TraceSummary {
  data: {
    spans: Span[];
    decisions: string[];
    funnel: Record<string, number>;
    candidates: Candidate[];
    tokens: Record<string, number>;
    error: { stage: string; type: string; message: string } | null;
    prompt: string | null;
  };
}

export interface Stats {
  requests: number;
  latency_p50_ms?: number;
  latency_p95_ms?: number;
  ttft_p50_ms?: number | null;
  cache_hit_rate?: number;
  abstain_rate?: number;
  error_rate?: number;
  verify_fail_rate?: number;
}

export interface ValidationResult {
  valid: boolean;
  errors: { loc: (string | number)[]; msg: string }[];
  warnings: { stage: string; msg: string }[];
  checks: Record<string, Record<string, unknown>>;
  requires_rebuild?: boolean;
}

export interface EvalReport {
  version: number;
  items: number;
  metrics: Record<string, number | null>;
  warnings: string[];
  results: { question: string; answerable: boolean; rank: number | null; abstained: boolean; confidence: number }[];
}

/** A stored run of a chatbot's test questions, and what triggered it. */
export interface EvalRun extends EvalReport {
  id: number;
  ran_at: number;
  reason: string;
}

export interface GoldenItem {
  question: string;
  expected_sources: string[];
}

export interface SchemaResponse {
  schema: JsonSchema;
  index_stages: string[];
  presets: { name: string; description: string }[];
  /** What changing a setting does, by "stage.field", then by value, "true"/"false", "up"/"down" or "*". */
  notes: Record<string, Record<string, string>>;
}

export interface JsonSchema {
  title?: string;
  description?: string;
  type?: string;
  properties?: Record<string, JsonSchema>;
  $defs?: Record<string, JsonSchema>;
  $ref?: string;
  enum?: string[];
  items?: JsonSchema;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  tier?: "essential" | "advanced";
  summary?: string;
  rebuild?: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
  }
}

const API_KEY_STORAGE = "ragkit.apiKey";
export const SIGNED_OUT = "archie:signed-out";

export type Role = "viewer" | "editor" | "owner";

export interface Me {
  accounts: boolean;
  role: Role | null;
  via: "account" | "key" | "open" | null;
  key_required: boolean;
  user: { id: string; email: string; name: string } | null;
}

export interface Person {
  id: string;
  email: string;
  name: string;
  role: Role;
  disabled: number;
  created_at: number;
  joined: number;
  invite_expires: number | null;
}

export interface AuditEntry {
  id: number;
  at: number;
  actor: string;
  action: string;
  target: string | null;
  status: number;
}

export function getApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setApiKey(value: string): void {
  try {
    if (value) localStorage.setItem(API_KEY_STORAGE, value);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    /* storage unavailable: the key lasts for this page only */
  }
}

function headers(json: boolean): HeadersInit {
  const result: Record<string, string> = {};
  if (json) result["Content-Type"] = "application/json";
  const key = getApiKey();
  if (key) result.Authorization = `Bearer ${key}`;
  return result;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData;
  const response = await fetch(path, { ...init, headers: { ...headers(!isForm && !!init.body), ...init.headers } });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* non-JSON error body */
    }
    // Signed out (session ended, account disabled): let the app show the sign-in screen.
    if (response.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export interface DocumentReport {
  reader?: "fast" | "layout";
  template?: string | null;
  pages?: number;
  ocr_pages?: number;
  tables?: number;
  layout_hints?: string[];
  warnings?: string[];
  chunks_produced?: number;
  duplicates?: number;
  flagged?: number;
  edited?: number;
  quality?: number;
}

export interface IndexedDocument {
  uri: string;
  content_hash: string;
  chunk_count: number;
  indexed_at: number;
  report: DocumentReport;
}

export interface DocumentChunk {
  id: string;
  ordinal: number;
  text: string;
  section: string;
  page_start: number;
  page_end: number;
  content_type: "text" | "table";
  template: string | null;
  token_count: number;
  enrichment: { context: string; questions: string[]; keywords: string[] } | null;
  excluded: boolean | null;
  /** Wording that looks like instructions to an AI (prompt-injection screening). */
  flagged: string | null;
  edited: boolean;
}

export interface Folder {
  name: string;
  path: string;
  files: number;
}

export interface FolderListing {
  path: string | null;
  parent: string | null;
  files: number;
  folders: Folder[];
}

export interface ModelCheck {
  provider: string;
  model: string;
  ok: boolean;
}

export interface SetupModels {
  embedding: ModelCheck;
  generation: ModelCheck;
  fallback_embedding: { provider: string; model: string } | null;
  installed: string[];
  /** Chat models of the connections added in Preferences. */
  answer_options: { provider: string; name: string; model: string }[];
}

export type ConnectionKind = "anthropic" | "openai" | "gemini" | "ollama" | "compatible";

/** Something chatbots can run on: a built-in, or a provider connected in Preferences. Keys never come back. */
export interface Connection {
  id: string;
  kind: ConnectionKind | "builtin";
  name: string;
  base_url: string;
  builtin: boolean;
  chat: boolean;
  embeddings: boolean;
  key_hint: string | null;
  /** False after a restore onto another machine: the key must be entered again. */
  readable: boolean;
}

/** Where a conversation's requests go: a chatbot (admin) or a share link (anyone with the link). */
export const chatBase = (kbId: string) => `/api/kbs/${kbId}`;
export const shareBase = (token: string) => `/api/share/${encodeURIComponent(token)}`;

const patch = (body: unknown): RequestInit => ({ method: "PATCH", body: JSON.stringify(body) });

export const api = {
  me: () => request<Me>("/api/auth/me"),
  signIn: (email: string, password: string) => request<Me>("/api/auth/sign-in", json({ email, password })),
  signOut: () => request<void>("/api/auth/sign-out", { method: "POST" }),
  createOwner: (email: string, name: string, password: string) =>
    request<Me>("/api/auth/owner", json({ email, name, password })),
  invitation: (token: string) =>
    request<{ email: string; joined: boolean }>(`/api/auth/invite/${encodeURIComponent(token)}`),
  acceptInvite: (token: string, name: string, password: string) =>
    request<Me>(`/api/auth/invite/${encodeURIComponent(token)}`, json({ name, password })),
  changePassword: (current: string, next: string) => request<void>("/api/auth/password", json({ current, new: next })),
  people: () => request<Person[]>("/api/users"),
  addPerson: (email: string, role: Role) =>
    request<{ user: Person; invite_token: string }>("/api/users", json({ email, role })),
  reinvite: (id: string) => request<{ invite_token: string }>(`/api/users/${id}/invite`, { method: "POST" }),
  changePerson: (id: string, change: { role?: Role; disabled?: boolean }) =>
    request<Person>(`/api/users/${id}`, patch(change)),
  removePerson: (id: string) => request<void>(`/api/users/${id}`, { method: "DELETE" }),
  sourceAccounts: () => request<SourceCatalog>("/api/source-accounts"),
  addSourceAccount: (body: {
    kind: string;
    name?: string;
    settings: Record<string, string>;
    secret: Record<string, string>;
  }) => request<{ account: SourceAccount; found: string }>("/api/source-accounts", json(body)),
  removeSourceAccount: (id: string) => request<void>(`/api/source-accounts/${id}`, { method: "DELETE" }),
  connections: () => request<Connection[]>("/api/connections"),
  addConnection: (body: { kind: ConnectionKind; name?: string; base_url?: string; api_key?: string }) =>
    request<{ connection: Connection; models: string[] }>("/api/connections", json(body)),
  removeConnection: (id: string) => request<void>(`/api/connections/${id}`, { method: "DELETE" }),
  audit: (limit = 100) => request<AuditEntry[]>(`/api/audit?limit=${limit}`),
  ready: () => request<{ ready: boolean; qdrant: boolean; ollama: boolean }>("/api/system/ready"),
  capabilities: () =>
    request<{ qdrant: { mode: string; hnsw: boolean }; auth: boolean; data_roots: string[] }>(
      "/api/system/capabilities",
    ),
  schema: () => request<SchemaResponse>("/api/schema"),
  preset: (name: string) => request<Config>(`/api/presets/${encodeURIComponent(name)}`),
  models: (provider: string) => request<{ models: string[] }>(`/api/models?provider=${encodeURIComponent(provider)}`),
  listKbs: () => request<KnowledgeBase[]>("/api/kbs"),
  getKb: (id: string) => request<KnowledgeBaseDetail>(`/api/kbs/${id}`),
  createKb: (name: string, preset: string, config: Config = {}) =>
    request<KnowledgeBase>("/api/kbs", json({ name, preset, config })),
  deleteKb: (id: string) => request<void>(`/api/kbs/${id}`, { method: "DELETE" }),
  versions: (id: string) => request<{ version: number; created_at: number }[]>(`/api/kbs/${id}/configs`),
  configVersion: (id: string, version: number) => request<Config>(`/api/kbs/${id}/configs/${version}`),
  saveConfig: (id: string, config: Config) =>
    request<{ version: number; requires_rebuild: boolean }>(`/api/kbs/${id}/configs`, json({ config })),
  validate: (id: string, config: Config) =>
    request<ValidationResult>(`/api/kbs/${id}/configs/validate`, json({ config })),
  startBuild: (id: string) => request<{ job_id: string }>(`/api/kbs/${id}/builds`, { method: "POST" }),
  builds: (id: string) => request<JobRef[]>(`/api/kbs/${id}/builds`),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<{ cancel_requested: boolean }>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  uploads: (id: string) => request<{ name: string; bytes: number }[]>(`/api/kbs/${id}/uploads`),
  upload: (id: string, files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request<{ saved: { name: string; bytes: number }[] }>(`/api/kbs/${id}/uploads`, {
      method: "POST",
      body: form,
    });
  },
  traces: (kbId: string, outcome?: string, limit = 100) =>
    request<TraceSummary[]>(
      `/api/traces?kb_id=${encodeURIComponent(kbId)}&limit=${limit}${outcome ? `&outcome=${outcome}` : ""}`,
    ),
  trace: (id: string) => request<Trace>(`/api/traces/${id}`),
  stats: (id: string) => request<Stats>(`/api/kbs/${id}/stats`),
  evaluate: (id: string, items: unknown[], version?: number) =>
    request<EvalRun>(`/api/kbs/${id}/eval`, json({ items, version })),
  evalRuns: (id: string) => request<EvalRun[]>(`/api/kbs/${id}/eval/runs`),
  golden: (id: string) => request<{ items: GoldenItem[]; updated_at: number | null }>(`/api/kbs/${id}/golden`),
  saveGolden: (id: string, items: GoldenItem[]) =>
    request<{ items: GoldenItem[] }>(`/api/kbs/${id}/golden`, { method: "PUT", body: JSON.stringify({ items }) }),
  draftGolden: (id: string, count = 10) =>
    request<{ items: GoldenItem[] }>(`/api/kbs/${id}/golden/draft`, json({ count })),
  suggestions: (base: string) => request<string[]>(`${base}/suggestions`),
  folders: (path?: string) =>
    request<FolderListing>(`/api/system/folders${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  setupModels: (preset: string) => request<SetupModels>(`/api/setup/models?preset=${encodeURIComponent(preset)}`),
  share: (id: string, enabled: boolean, reset = false) =>
    request<{ share_token: string | null }>(`/api/kbs/${id}/share`, json({ enabled, reset })),
  shared: (token: string) => request<{ name: string; ready: boolean; documents: number }>(shareBase(token)),
  slo: (kbId?: string) =>
    request<{ slos: Slo[]; alerts: Alert[] }>(`/api/slo${kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ""}`),
  documents: (kbId: string) => request<IndexedDocument[]>(`/api/kbs/${kbId}/documents`),
  documentChunks: (kbId: string, uri: string) =>
    request<{ uri: string; title: string; chunks: DocumentChunk[] }>(
      `/api/kbs/${kbId}/documents/chunks?uri=${encodeURIComponent(uri)}`,
    ),
  editChunk: (kbId: string, edit: { uri: string; ordinal: number; text?: string; excluded: boolean }) =>
    request<{ ordinal: number; text: string; excluded: boolean }>(`/api/kbs/${kbId}/documents/chunks`, {
      method: "PATCH",
      body: JSON.stringify(edit),
    }),
  restoreChunk: (kbId: string, uri: string, ordinal: number) =>
    request<void>(`/api/kbs/${kbId}/documents/chunks?uri=${encodeURIComponent(uri)}&ordinal=${ordinal}`, {
      method: "DELETE",
    }),
  feedback: (base: string, traceId: string, rating: "up" | "down" | null) =>
    request<void>(`${base}/feedback`, json({ trace_id: traceId, rating })),
  gaps: () => request<Gap[]>("/api/gaps"),
  clearSession: (base: string, sessionId: string) =>
    request<void>(`${base}/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
};

/** Parse one Server-Sent Events block (lines separated by \n, block terminated by a blank line). */
export function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? { event, data: data.join("\n") } : null;
}

/** Stream SSE events from a fetch response; works with POST bodies and auth headers, unlike EventSource. */
export async function* readSse<T>(response: Response, signal?: AbortSignal): AsyncGenerator<T> {
  if (!response.body) return;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (!signal?.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const parsed = parseSseBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (parsed) yield JSON.parse(parsed.data) as T;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function streamChat(
  base: string,
  body: { query: string; session_id: string; profile: Record<string, string> },
  signal: AbortSignal,
): Promise<AsyncGenerator<ChatEvent>> {
  const response = await fetch(`${base}/chat`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, detail);
  }
  return readSse<ChatEvent>(response, signal);
}

export async function streamJob(jobId: string, signal: AbortSignal): Promise<AsyncGenerator<Job>> {
  const response = await fetch(`/api/jobs/${jobId}/events`, { headers: headers(false), signal });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return readSse<Job>(response, signal);
}
