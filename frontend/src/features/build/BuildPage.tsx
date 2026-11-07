import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronRight, Circle, Loader2, MinusCircle, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Button,
  buttonClass,
  cn,
  EmptyState,
  ErrorNote,
  Group,
  PageHeader,
  Skeleton,
  StatusBadge,
} from "../../components/ui";
import { api, type Job, type JobStage, streamJob } from "../../lib/api";
import { duration, formatMs, formatNumber, relativeTime } from "../../lib/format";

/** Plain names and one-line explanations for each build stage. */
export const STAGES: Record<string, [string, string]> = {
  validate: ["Check", "The documents can be read and the AI model answers."],
  provision: ["Prepare a new index", "A fresh place to store what Archie learns."],
  discover: ["Find documents", "Unchanged documents from the last build are skipped."],
  parse: ["Read documents", "Text, headings and tables are extracted."],
  chunk: ["Split into passages", "Documents become short passages Archie can quote."],
  enrich: ["Describe passages", "An AI model notes what each passage is about, so search finds it more often."],
  embed: ["Understand passages", "Each passage is turned into a meaning fingerprint."],
  index: ["Store passages", "Passages are saved for fast searching."],
  reconcile: ["Tidy up", "Documents removed from the folder are forgotten."],
  smoke_test: ["Test searches", "A few sample searches must find something."],
  activate: ["Switch over", "Chat starts using the new index."],
};

export const TERMINAL = new Set(["completed", "completed_with_errors", "failed", "cancelled"]);

/** A job, kept live over Server-Sent Events while it runs; finishing refreshes the chatbot everywhere. */
export function useLiveJob(jobId: string | undefined | null): { job: Job | undefined; error: unknown } {
  const queryClient = useQueryClient();
  const initial = useQuery({ queryKey: ["job", jobId], queryFn: () => api.job(jobId as string), enabled: !!jobId });
  const [live, setLive] = useState<Job>();
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setLive(undefined);
    if (!jobId) return;
    const controller = new AbortController();
    (async () => {
      try {
        for await (const job of await streamJob(jobId, controller.signal)) {
          setLive(job);
          if (TERMINAL.has(job.status)) {
            queryClient.invalidateQueries({ queryKey: ["kb", job.kb_id] });
            queryClient.invalidateQueries({ queryKey: ["builds", job.kb_id] });
            queryClient.invalidateQueries({ queryKey: ["suggestions", job.kb_id] });
            queryClient.invalidateQueries({ queryKey: ["kbs"] });
          }
        }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught);
      }
    })();
    return () => controller.abort();
  }, [jobId, queryClient]);
  return { job: live ?? initial.data, error: error ?? initial.error };
}

/** Where a running build is, in one sentence and a progress bar. Shared by Builds and Ask. */
export function BuildProgress({ job }: { job: Job }) {
  const done = job.stages.filter((stage) => stage.status !== "pending" && stage.status !== "running").length;
  const reading = job.stages.find((stage) => stage.stage === "parse");
  const current = job.stages.find((stage) => stage.status === "running");
  return (
    <div aria-live="polite">
      <div className="flex items-center gap-2.5">
        <Loader2 className="size-4 animate-spin text-accent" aria-hidden />
        <span className="text-base font-medium">{STAGES[current?.stage ?? ""]?.[0] ?? "Getting started"}…</span>
      </div>
      <p className="mt-1 text-sm text-slate">
        {reading && reading.items_in > 0
          ? `${reading.items_out + reading.items_failed} of ${reading.items_in} documents read.`
          : "Archie is getting ready to read your documents."}
      </p>
      <div
        className="mt-3 h-1 overflow-hidden rounded-full bg-plinth"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={job.stages.length}
        aria-valuenow={done}
        aria-label="Build progress"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500 ease-[var(--ease-out)]"
          style={{ width: `${(done / Math.max(1, job.stages.length)) * 100}%` }}
        />
      </div>
    </div>
  );
}

function StageIcon({ stage }: { stage: JobStage }) {
  const warned = stage.items_failed > 0 || ((stage.detail?.warnings as string[] | undefined)?.length ?? 0) > 0;
  if (stage.status === "completed" && warned)
    return <AlertTriangle className="size-[18px] text-warn" aria-label="Finished with warnings" />;
  if (stage.status === "completed") return <CheckCircle2 className="size-[18px] text-ok" aria-label="Finished" />;
  if (stage.status === "failed") return <XCircle className="size-[18px] text-bad" aria-label="Failed" />;
  if (stage.status === "running")
    return <Loader2 className="size-[18px] animate-spin text-accent" aria-label="In progress" />;
  if (stage.status === "skipped") return <MinusCircle className="size-[18px] text-faint" aria-label="Skipped" />;
  return <Circle className="size-[18px] text-rule-strong" aria-label="Waiting" />;
}

function StageRow({ stage }: { stage: JobStage }) {
  const [name, explanation] = STAGES[stage.stage] ?? [stage.stage, ""];
  const ms = duration(stage.started_at, stage.finished_at);
  const warnings = (stage.detail?.warnings as string[] | undefined) ?? [];
  const detail = Object.entries(stage.detail ?? {}).filter(([key]) => key !== "warnings");
  const counts = stage.items_in > 0 || stage.items_out > 0;
  return (
    <li className="grid grid-cols-[1.125rem_minmax(0,1fr)_auto] gap-x-3.5 px-4 py-3">
      <span className="pt-0.5">
        <StageIcon stage={stage} />
      </span>
      <div className="min-w-0">
        <div className="text-base font-medium">{name}</div>
        <div className="text-sm text-slate">{explanation}</div>
        {counts && (
          <div className="mt-0.5 text-sm text-slate">
            {formatNumber(stage.items_in)} in, {formatNumber(stage.items_out)} out
            {stage.items_skipped > 0 && <span> · {formatNumber(stage.items_skipped)} unchanged</span>}
            {stage.items_failed > 0 && (
              <span className="font-medium text-bad"> · {formatNumber(stage.items_failed)} failed</span>
            )}
          </div>
        )}
        {warnings.map((warning) => (
          <p key={warning} className="mt-1 text-sm text-warn">
            {warning}
          </p>
        ))}
        {detail.length > 0 && (
          <details className="group mt-1 text-xs text-slate">
            <summary className="flex cursor-default list-none items-center gap-0.5 select-none hover:text-ink [&::-webkit-details-marker]:hidden">
              <ChevronRight className="size-3.5 transition-transform duration-200 group-open:rotate-90" aria-hidden />
              Technical details
            </summary>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 font-mono">
              {detail.map(([key, value]) => (
                <div key={key} className="contents">
                  <dt>{key}</dt>
                  <dd className="truncate">{String(value)}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
      <div className="pt-0.5 font-mono text-xs text-slate tabular-nums">{ms !== null ? formatMs(ms) : ""}</div>
    </li>
  );
}

function Outcome({ job, kbId }: { job: Job; kbId: string }) {
  const summary = job.summary ?? {};
  if (job.status === "completed" || job.status === "completed_with_errors") {
    return (
      <section className="settle">
        <div className="flex items-center gap-3">
          <CheckCircle2 className="size-7 text-ok" aria-hidden />
          <h2 className="font-display text-xl font-semibold tracking-tight">
            {job.status === "completed" ? "Ready to answer" : "Ready, with some documents skipped"}
          </h2>
        </div>
        <p className="mt-2 text-base text-slate">
          Archie read {formatNumber(Number(summary.documents ?? 0))} documents (
          {formatNumber(Number(summary.pages ?? 0))} pages) in {formatMs(duration(job.started_at, job.finished_at))} and
          can now quote {formatNumber(Number(summary.total_chunks ?? 0))} passages from them.
          {job.failures.length > 0 && ` ${job.failures.length} couldn't be read; they're listed below.`}
        </p>
        <Link to={`/kb/${kbId}/ask`} className={cn(buttonClass("primary"), "mt-5")}>
          Start Asking
        </Link>
      </section>
    );
  }
  if (job.status === "failed") {
    return (
      <section role="alert" className="rounded-[var(--radius-group)] bg-bad-soft px-5 py-4">
        <h2 className="text-lg font-semibold text-bad">The build stopped</h2>
        <p className="mt-1 text-base text-ink">{job.error ?? "Something went wrong."}</p>
        <Link to={`/kb/${kbId}/manage/settings`} className={cn(buttonClass("secondary"), "mt-4")}>
          Check Settings
        </Link>
      </section>
    );
  }
  if (job.status === "cancelled") {
    return <p className="text-base text-slate">This build was cancelled. Ask still uses the previous version.</p>;
  }
  return (
    <section className="rounded-[var(--radius-group)] bg-panel px-5 py-4 shadow-[0_0_0_0.5px_var(--color-rule)]">
      <BuildProgress job={job} />
      <p className="mt-3 text-sm text-slate">You can leave this page; Ask keeps using the previous version.</p>
    </section>
  );
}

export function BuildPage() {
  const { kbId = "", jobId: routeJobId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const builds = useQuery({ queryKey: ["builds", kbId], queryFn: () => api.builds(kbId), refetchInterval: 5_000 });
  const jobId = routeJobId ?? builds.data?.[0]?.id;
  const { job, error } = useLiveJob(jobId);
  const start = useMutation({
    mutationFn: () => api.startBuild(kbId),
    onSuccess: ({ job_id }) => {
      queryClient.invalidateQueries({ queryKey: ["builds", kbId] });
      navigate(`/kb/${kbId}/manage/builds/${job_id}`);
    },
  });
  const cancel = useMutation({ mutationFn: () => api.cancelJob(jobId as string) });
  const running = job && !TERMINAL.has(job.status);

  return (
    <>
      <PageHeader
        title="Builds"
        description="A build reads your documents so Archie can answer from them."
        actions={
          running ? (
            <Button variant="danger" busy={cancel.isPending} onClick={() => cancel.mutate()}>
              Cancel Build
            </Button>
          ) : (
            <Button
              variant={job?.status === "completed" ? "secondary" : "primary"}
              busy={start.isPending}
              onClick={() => start.mutate()}
            >
              Build Now
            </Button>
          )
        }
      />
      <div className="grid gap-8 px-5 pb-16 md:px-8 lg:grid-cols-[minmax(0,1fr)_16rem]">
        <div className="min-w-0 space-y-7">
          <ErrorNote error={start.error ?? error} />
          {builds.isLoading && <Skeleton />}
          {!builds.isLoading && !jobId && (
            <EmptyState title="Not built yet">
              Check the documents and settings, then choose Build Now. It usually takes a few minutes.
            </EmptyState>
          )}
          {job && (
            <>
              <Outcome job={job} kbId={kbId} />
              {job.failures.length > 0 && (
                <Group title="Documents that couldn't be read">
                  {job.failures.map((failure) => (
                    <div key={failure.document + failure.stage} className="px-4 py-2.5">
                      <div className="text-base font-medium">{failure.document}</div>
                      <div className="text-sm text-slate">{failure.error}</div>
                    </div>
                  ))}
                </Group>
              )}
              <section>
                <h2 className="mb-1.5 px-3 text-sm font-semibold text-slate">Steps</h2>
                <ol className="divide-y divide-rule overflow-hidden rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)]">
                  {job.stages.map((stage) => (
                    <StageRow key={stage.stage} stage={stage} />
                  ))}
                </ol>
              </section>
            </>
          )}
        </div>
        <aside aria-label="Earlier builds">
          <h2 className="mb-1.5 px-3 text-sm font-semibold text-slate">History</h2>
          <ul className="overflow-hidden rounded-[var(--radius-group)] bg-panel p-1 shadow-[0_0_0_0.5px_var(--color-rule)]">
            {builds.data?.map((item) => (
              <li key={item.id}>
                <Link
                  to={`/kb/${kbId}/manage/builds/${item.id}`}
                  aria-current={item.id === jobId ? "page" : undefined}
                  className={cn(
                    "flex items-center justify-between gap-2 rounded-md px-2.5 py-2 hover:bg-plinth",
                    item.id === jobId && "bg-accent-soft",
                  )}
                >
                  <span className="text-sm">
                    {relativeTime(item.created_at)}
                    <span className="block text-xs text-slate">Settings version {item.config_version}</span>
                  </span>
                  <StatusBadge status={item.status} />
                </Link>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </>
  );
}
