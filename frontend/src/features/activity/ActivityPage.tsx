import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { TracePanel } from "../../components/TracePanel";
import { Dialog, EmptyState, ErrorNote, PageHeader, Select, Skeleton, Stat, StatusBadge } from "../../components/ui";
import { api } from "../../lib/api";
import { formatMs, formatPercent, relativeTime } from "../../lib/format";

const OUTCOMES: [string, string][] = [
  ["", "Every question"],
  ["answered", "Answered"],
  ["cache_hit", "Answered from memory"],
  ["abstained", "Declined"],
  ["error", "Errors"],
];

export function ActivityPage() {
  const { kbId = "" } = useParams();
  const [outcome, setOutcome] = useState("");
  const [traceId, setTraceId] = useState<string | null>(null);
  const stats = useQuery({ queryKey: ["stats", kbId], queryFn: () => api.stats(kbId), refetchInterval: 10_000 });
  const traces = useQuery({
    queryKey: ["traces", kbId, outcome],
    queryFn: () => api.traces(kbId, outcome || undefined),
    refetchInterval: 10_000,
  });
  const s = stats.data;

  return (
    <>
      <PageHeader
        title="Activity"
        description="Every question asked here, how it was answered, and how long it took. Open one to see exactly how Archie found its answer."
        actions={
          <Select aria-label="Show" value={outcome} onChange={(e) => setOutcome(e.target.value)} className="w-56">
            {OUTCOMES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        }
      />
      <div className="space-y-8 px-5 pb-16 md:px-10">
        <ErrorNote error={stats.error ?? traces.error} />
        {s && s.requests > 0 && (
          <div className="grid grid-cols-2 gap-x-8 gap-y-5 md:grid-cols-4">
            <Stat
              label="Typical answer time"
              value={formatMs(s.latency_p50_ms)}
              hint={`Slowest 5%: ${formatMs(s.latency_p95_ms)}`}
            />
            <Stat label="Answered from memory" value={formatPercent(s.cache_hit_rate)} hint="Repeated questions" />
            <Stat label="Declined to answer" value={formatPercent(s.abstain_rate)} hint="Not in the documents" />
            <Stat
              label="Errors"
              value={formatPercent(s.error_rate)}
              hint={s.requests === 1 ? "Last question" : `Last ${s.requests} questions`}
            />
          </div>
        )}
        {traces.isLoading && <Skeleton lines={5} />}
        {traces.data?.length === 0 && (
          <EmptyState title={outcome ? "No questions with this outcome" : "No questions yet"}>
            Questions asked in Ask appear here with their full explanation.
          </EmptyState>
        )}
        {traces.data && traces.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-rule text-xs text-slate">
                <tr>
                  <th className="py-2 pr-4 font-semibold">Question</th>
                  <th className="py-2 pr-4 font-semibold">Outcome</th>
                  <th className="py-2 pr-4 text-right font-semibold">Time</th>
                  <th className="py-2 pr-4 text-right font-semibold">Confidence</th>
                  <th className="py-2 font-semibold">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {traces.data.map((trace) => (
                  <tr key={trace.id} className="hover:bg-plinth">
                    <td className="max-w-md py-3 pr-4">
                      <button
                        type="button"
                        onClick={() => setTraceId(trace.id)}
                        className="truncate text-left font-semibold hover:text-accent hover:underline"
                      >
                        {trace.query}
                      </button>
                    </td>
                    <td className="py-3 pr-4">
                      <StatusBadge status={trace.outcome} />
                    </td>
                    <td className="py-3 pr-4 text-right font-mono tabular-nums">{formatMs(trace.total_ms)}</td>
                    <td className="py-3 pr-4 text-right font-mono tabular-nums">
                      {trace.confidence === null ? "–" : `${Math.round(trace.confidence * 100)}%`}
                    </td>
                    <td className="py-3 text-slate">{relativeTime(trace.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <Dialog open={!!traceId} onClose={() => setTraceId(null)} title="How Archie found this" variant="side">
        {traceId && <TracePanel traceId={traceId} />}
      </Dialog>
    </>
  );
}
