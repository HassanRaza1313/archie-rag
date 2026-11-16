import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, MessageCircleQuestion } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, cn, EmptyState, ErrorNote, PageHeader, Skeleton } from "../../components/ui";
import { type Alert, api, type Gap, type Slo } from "../../lib/api";
import { relativeTime } from "../../lib/format";

const ALERT_TEXT: Record<string, string> = {
  availability: "More questions are failing than the promise allows",
  latency: "Answers are starting more slowly than the promise allows",
};

function sloTitle(slo: Slo): string {
  return slo.name === "availability" ? "Questions get an answer" : "Answers start quickly";
}

function Objective({ slo }: { slo: Slo }) {
  const budget = slo.error_budget_remaining;
  const percent = budget === null ? null : Math.max(0, Math.round(budget * 100));
  const tone = slo.met === null ? "text-slate" : slo.met ? "text-ok" : "text-bad";
  return (
    <section className="rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)] px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{sloTitle(slo)}</h2>
        <span className={cn("text-sm font-semibold", tone)}>
          {slo.met === null ? "No questions yet" : slo.met ? "Keeping its promise" : "Missing its promise"}
        </span>
      </div>
      <p className="mt-1 text-sm text-slate">
        Promise: {slo.description} (last {slo.window_days} days).
      </p>
      <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-3">
        <div>
          <div className="text-xs font-semibold text-slate">Achieved</div>
          <div className="mt-1 font-mono text-lg tabular-nums">
            {slo.sli === null ? "–" : `${(slo.sli * 100).toFixed(2)}%`}
          </div>
        </div>
        <div>
          <div className="text-xs font-semibold text-slate">Questions counted</div>
          <div className="mt-1 font-mono text-lg tabular-nums">{slo.requests}</div>
        </div>
        <div className="col-span-2 sm:col-span-1">
          <div className="text-xs font-semibold text-slate">Room for problems left</div>
          <div className="mt-1 font-mono text-lg tabular-nums">{percent === null ? "–" : `${percent}%`}</div>
          <div className="mt-2 h-1.5 rounded-full bg-plinth" aria-hidden>
            <div
              className={cn("h-full rounded-full", (percent ?? 100) > 25 ? "bg-ok" : "bg-bad")}
              style={{ width: `${percent ?? 0}%` }}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function AlertRow({ alert, names }: { alert: Alert; names: Map<string, string> }) {
  const title =
    alert.name === "build_failed"
      ? `Build failed: ${names.get(alert.kb_id ?? "") ?? "a chatbot"}`
      : (ALERT_TEXT[alert.slo ?? ""] ?? alert.name);
  return (
    <li className="flex items-start gap-3 py-4">
      <AlertTriangle className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-semibold">{title}</div>
        <div className="text-sm text-slate">
          {alert.name === "build_failed"
            ? alert.detail
            : `Burning ${alert.burn_rate}× faster than allowed over ${alert.window}, across ${alert.requests} questions.`}
        </div>
      </div>
      <Badge tone={alert.severity === "page" ? "bad" : "warn"}>
        {alert.severity === "page" ? "Urgent" : "Look soon"}
      </Badge>
      {alert.kb_id && (
        <Link to={`/kb/${alert.kb_id}/manage/builds`} className="text-sm font-medium text-accent hover:underline">
          Open
        </Link>
      )}
    </li>
  );
}

function gapDetail(gap: Gap, name: string | undefined): string {
  const parts = [name ?? "A chatbot", `asked ${gap.asked === 1 ? "once" : `${gap.asked} times`}`];
  if (gap.unanswered) parts.push(gap.unanswered === gap.asked ? "never answered" : `${gap.unanswered} unanswered`);
  if (gap.unhelpful) parts.push(`${gap.unhelpful} rated not helpful`);
  return [...parts, relativeTime(gap.last_asked)].join(" · ");
}

function Gaps({ gaps, names }: { gaps: Gap[]; names: Map<string, string> }) {
  return (
    <section aria-labelledby="gaps">
      <h2 id="gaps" className="text-lg font-semibold">
        What people couldn't find
      </h2>
      <p className="mt-1 text-sm text-slate">
        Questions from the last 30 days that Archie couldn't answer or people rated not helpful, most asked first.
        Adding documents that cover them fills the gap.
      </p>
      {gaps.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-ok">
          <CheckCircle2 className="size-5" aria-hidden /> Every question found an answer.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-rule px-4 rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)]">
          {gaps.map((gap) => (
            <li key={`${gap.kb_id}-${gap.question}`} className="flex items-start gap-3 py-3.5">
              <MessageCircleQuestion className="mt-0.5 size-5 shrink-0 text-slate" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="font-medium break-words">{gap.question}</div>
                <div className="text-sm text-slate">{gapDetail(gap, names.get(gap.kb_id))}</div>
              </div>
              <Link
                to={`/kb/${gap.kb_id}/manage/documents`}
                className="shrink-0 text-sm font-medium text-accent hover:underline"
              >
                Documents
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function HealthPage() {
  const report = useQuery({ queryKey: ["slo"], queryFn: () => api.slo(), refetchInterval: 30_000 });
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: api.listKbs });
  const gaps = useQuery({ queryKey: ["gaps"], queryFn: api.gaps, refetchInterval: 60_000 });
  const names = new Map((kbs.data ?? []).map((kb) => [kb.id, kb.name]));
  const firing = report.data?.alerts.filter((alert) => alert.state === "firing") ?? [];

  return (
    <div className="min-h-full bg-wall">
      <PageHeader
        title="Status"
        description="Archie's promises to the people asking questions, and anything that needs your attention."
      />
      <div className="space-y-8 px-5 pb-16 md:px-8">
        <ErrorNote error={report.error} />
        {report.isLoading && <Skeleton lines={5} />}
        {report.data && (
          <section aria-labelledby="alerts">
            <h2 id="alerts" className="text-lg font-semibold">
              Needs attention
            </h2>
            {firing.length === 0 ? (
              <p className="mt-3 flex items-center gap-2 text-sm text-ok">
                <CheckCircle2 className="size-5" aria-hidden /> Nothing needs attention right now.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-rule px-4 rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)]">
                {firing.map((alert) => (
                  <AlertRow key={`${alert.name}-${alert.kb_id ?? ""}`} alert={alert} names={names} />
                ))}
              </ul>
            )}
          </section>
        )}
        {report.data && (
          <div className="grid gap-4 lg:grid-cols-2">
            {report.data.slos.map((slo) => (
              <Objective key={slo.name} slo={slo} />
            ))}
          </div>
        )}
        {gaps.data && report.data?.slos.some((slo) => slo.requests > 0) && <Gaps gaps={gaps.data} names={names} />}
        {report.data?.slos.every((slo) => slo.requests === 0) && (
          <EmptyState title="Nothing measured yet">
            Status fills in as people ask questions. Detailed metrics are also available to monitoring tools at{" "}
            <code className="font-mono">/metrics</code>.
          </EmptyState>
        )}
      </div>
    </div>
  );
}
