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
  document_uri?: string | null;
  section: string | null;
  page_start: number | null;
  page_end: number | null;
  relevance: number;
  preview: string;
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
      summary?: AnswerSummary;
      trace_id: string;
    }
  | { type: "error"; message: string; trace_id?: string };

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

export interface SchemaResponse {
  schema: JsonSchema;
  index_stages: string[];
  presets: { name: string; description: string }[];
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
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
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
    request<EvalReport>(`/api/kbs/${id}/eval`, json({ items, version })),
  suggestions: (id: string) => request<string[]>(`/api/kbs/${id}/suggestions`),
  slo: (kbId?: string) =>
    request<{ slos: Slo[]; alerts: Alert[] }>(`/api/slo${kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ""}`),
  clearSession: (kbId: string, sessionId: string) =>
    request<void>(`/api/kbs/${kbId}/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
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
  kbId: string,
  body: { query: string; session_id: string; profile: Record<string, string> },
  signal: AbortSignal,
): Promise<AsyncGenerator<ChatEvent>> {
  const response = await fetch(`/api/kbs/${kbId}/chat`, {
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
