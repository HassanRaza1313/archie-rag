/** One request's trace, in plain words: what was searched, what Archie decided, and where the time went. */
import { useQuery } from "@tanstack/react-query";
import { api, type Trace } from "../lib/api";
import { formatMs } from "../lib/format";
import { Badge, ErrorNote, GalleryLabel, Skeleton, StatusBadge } from "./ui";

const FUNNEL: [string, string][] = [
  ["dense", "Found by meaning"],
  ["keyword", "Found by keywords"],
  ["fused", "Combined"],
  ["reranked", "Re-scored for relevance"],
  ["relevant", "Relevant enough"],
  ["deduplicated", "After removing repeats"],
  ["context", "Given to the AI"],
];

const SPAN_NAMES: Record<string, string> = {
  condense: "Read the conversation",
  embed: "Understand the question",
  cache: "Check earlier answers",
  decompose: "Split the question",
  retrieve: "Search documents",
  rewrite: "Rephrase the search",
  generate: "Write the answer",
  verify: "Check sources",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-rule px-5 py-5">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

export function Funnel({ funnel }: { funnel: Record<string, number> }) {
  const steps = FUNNEL.filter(([key]) => key in funnel);
  const max = Math.max(1, ...steps.map(([key]) => funnel[key] ?? 0));
  return (
    <div className="space-y-2">
      {steps.map(([key, label]) => {
        const value = funnel[key] ?? 0;
        return (
          <div key={key} className="grid grid-cols-[11rem_minmax(0,1fr)_2.5rem] items-center gap-3 text-sm">
            <span className="text-slate">{label}</span>
            <div className="h-2 rounded-full bg-plinth">
              <div className="h-2 rounded-full bg-accent" style={{ width: `${(value / max) * 100}%` }} />
            </div>
            <span className="text-right font-mono tabular-nums">{value}</span>
          </div>
        );
      })}
      {"context_tokens" in funnel && (
        <p className="pt-1 text-xs text-slate">
          <span className="font-mono">{funnel.context_tokens}</span> tokens of document text went to the AI.
        </p>
      )}
    </div>
  );
}

function Waterfall({ trace }: { trace: Trace }) {
  const total = Math.max(1, trace.total_ms);
  return (
    <div className="space-y-1.5">
      {trace.data.spans.map((span) => (
        <div
          key={`${span.name}-${span.start_ms}`}
          className="grid grid-cols-[11rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm"
        >
          <span className={span.error ? "text-bad" : "text-slate"}>{SPAN_NAMES[span.name] ?? span.name}</span>
          <div className="relative h-3 rounded bg-plinth">
            <div
              className={span.error ? "absolute h-3 rounded bg-bad" : "absolute h-3 rounded bg-accent"}
              style={{
                left: `${(span.start_ms / total) * 100}%`,
                width: `max(3px, ${(span.duration_ms / total) * 100}%)`,
              }}
              title={span.error ?? undefined}
            />
          </div>
          <span className="text-right font-mono text-xs tabular-nums">{formatMs(span.duration_ms)}</span>
        </div>
      ))}
    </div>
  );
}

export function TraceView({ trace }: { trace: Trace }) {
  const facts: [string, string][] = [
    ["Total time", formatMs(trace.total_ms)],
    ["First words after", formatMs(trace.ttft_ms)],
    ["Confidence", trace.confidence === null ? "–" : `${Math.round(trace.confidence * 100)}%`],
    ["Rephrased searches", String(trace.retries)],
    [
      "Tokens in → out",
      trace.data.tokens.prompt ? `${trace.data.tokens.prompt} → ${trace.data.tokens.completion}` : "–",
    ],
    ["Settings version", `v${trace.config_version}`],
  ];
  return (
    <div className="text-sm">
      <div className="border-b border-rule px-5 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={trace.outcome} />
          {trace.verify === "pass" && <Badge tone="ok">Every claim cites a source</Badge>}
          {trace.verify === "fail" && <Badge tone="warn">Some claims have no source</Badge>}
        </div>
        <p className="mt-3 text-base font-semibold">{trace.query}</p>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-slate">{label}</dt>
              <dd className="font-mono tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 font-mono text-xs text-slate">Request {trace.id}</p>
      </div>
      {trace.data.error && (
        <Section title="What went wrong">
          <p className="text-bad">
            {SPAN_NAMES[trace.data.error.stage] ?? trace.data.error.stage}: {trace.data.error.message}
          </p>
        </Section>
      )}
      {Object.keys(trace.data.funnel).length > 0 && (
        <Section title="What Archie searched">
          <Funnel funnel={trace.data.funnel} />
        </Section>
      )}
      {trace.data.decisions.length > 0 && (
        <Section title="What it decided">
          <ol className="list-decimal space-y-1 pl-5 marker:text-slate">
            {trace.data.decisions.map((decision, index) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: decisions are an append-only log
              <li key={index}>{decision}</li>
            ))}
          </ol>
        </Section>
      )}
      <Section title="Where the time went">
        <Waterfall trace={trace} />
      </Section>
      {trace.data.candidates.length > 0 && (
        <Section title="Best passages it considered">
          <ol className="space-y-2">
            {trace.data.candidates.map((candidate) => (
              <GalleryLabel
                as="li"
                key={candidate.chunk_id}
                mark={`#${candidate.rank} · ${Math.round(candidate.relevance * 100)}% relevant`}
                title={candidate.file ?? "Document"}
              >
                {[candidate.section, candidate.page ? `p. ${candidate.page}` : null].filter(Boolean).join(" · ")}
                <span className="block font-mono">
                  meaning rank {candidate.dense_rank ?? "–"} · keyword rank {candidate.keyword_rank ?? "–"}
                </span>
                {candidate.text && <span className="mt-1 line-clamp-3 block text-ink">{candidate.text}</span>}
              </GalleryLabel>
            ))}
          </ol>
        </Section>
      )}
      {trace.data.prompt && (
        <Section title="Exact prompt sent to the AI">
          <pre className="max-h-80 overflow-auto rounded-lg bg-plinth p-3 font-mono text-xs whitespace-pre-wrap">
            {trace.data.prompt}
          </pre>
        </Section>
      )}
    </div>
  );
}

export function TracePanel({ traceId }: { traceId: string }) {
  const trace = useQuery({ queryKey: ["trace", traceId], queryFn: () => api.trace(traceId), retry: 2 });
  if (trace.isLoading) {
    return (
      <div className="p-5">
        <Skeleton lines={6} />
      </div>
    );
  }
  if (trace.error) {
    return (
      <div className="p-5">
        <ErrorNote error={trace.error} />
      </div>
    );
  }
  return trace.data ? <TraceView trace={trace.data} /> : null;
}
