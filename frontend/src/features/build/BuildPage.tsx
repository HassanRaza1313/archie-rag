import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, CheckCircle2, Circle, Loader2, MinusCircle, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Button,
  buttonClass,
  cn,
  EmptyState,
  ErrorNote,
  PageHeader,
  Skeleton,
  Stat,
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
  embed: ["Understand passages", "Each passage is turned into a meaning fingerprint."],
  index: ["Store passages", "Passages are saved for fast searching."],
  reconcile: ["Tidy up", "Documents removed from the folder are forgotten."],
  smoke_test: ["Test searches", "A few sample searches must find something."],
  activate: ["Switch over", "Chat starts using the new index."],
};

const TERMINAL = new Set(["completed", "completed_with_errors", "failed", "cancelled"]);

function useLiveJob(jobId: string | undefined): { job: Job | undefined; error: unknown } {
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

function StageIcon({ stage }: { stage: JobStage }) {
  const warned = stage.items_failed > 0 || ((stage.detail?.warnings as string[] | undefined)?.length ?? 0) > 0;
  if (stage.status === "completed" && warned)
    return <AlertTriangle className="size-5 text-warn" aria-label="Finished with warnings" />;
  if (stage.status === "completed") return <CheckCircle2 className="size-5 text-ok" aria-label="Finished" />;
  if (stage.status === "failed") return <XCircle className="size-5 text-bad" aria-label="Failed" />;
  if (stage.status === "running")
    return <Loader2 className="size-5 animate-spin text-accent" aria-label="In progress" />;
  if (stage.status === "skipped") return <MinusCircle className="size-5 text-slate" aria-label="Skipped" />;
  return <Circle className="size-5 text-rule-strong" aria-label="Waiting" />;
}

function StageRow({ stage }: { stage: JobStage }) {
  const [name, explanation] = STAGES[stage.stage] ?? [stage.stage, ""];
  const ms = duration(stage.started_at, stage.finished_at);
  const warnings = (stage.detail?.warnings as string[] | undefined) ?? [];
  const detail = Object.entries(stage.detail ?? {}).filter(([key]) => key !== "warnings");
  const counts = stage.items_in > 0 || stage.items_out > 0;
  return (
    <li className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] gap-x-4 py-4">
      <StageIcon stage={stage} />
      <div className="min-w-0">
        <div className="font-semibold">{name}</div>
        <div className="text-sm text-slate">{explanation}</div>
        {counts && (
          <div className="mt-1 text-sm">
            {formatNumber(stage.items_in)} in → {formatNumber(stage.items_out)} out
            {stage.items_skipped > 0 && (
              <span className="text-slate"> · {formatNumber(stage.items_skipped)} unchanged</span>
            )}
            {stage.items_failed > 0 && (
              <span className="font-semibold text-bad"> · {formatNumber(stage.items_failed)} failed</span>
            )}
          </div>
        )}
        {warnings.map((warning) => (
          <p key={warning} className="mt-1 text-sm text-warn">
            {warning}
          </p>
        ))}
        {detail.length > 0 && (
          <details className="mt-1 text-xs text-slate">
            <summary className="cursor-pointer select-none hover:text-ink">Technical details</summary>
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
      <div className="font-mono text-xs text-slate tabular-nums">{ms !== null ? formatMs(ms) : ""}</div>
    </li>
  );
}

function Outcome({ job, kbId }: { job: Job; kbId: string }) {
  const summary = job.summary ?? {};
  if (job.status === "completed" || job.status === "completed_with_errors") {
    return (
      <section className="rounded-xl border border-rule bg-panel p-6 md:p-8">
        <h2 className="text-xl font-semibold tracking-tight">
          {job.status === "completed" ? "Ready to answer" : "Ready, with some documents skipped"}
        </h2>
        <p className="mt-2 text-sm text-slate">
          Archie read {formatNumber(Number(summary.documents ?? 0))} documents and can now quote{" "}
          {formatNumber(Number(summary.total_chunks ?? 0))} passages from them.
          {job.failures.length > 0 && ` ${job.failures.length} couldn't be read; they're listed below.`}
        </p>
        <Link to={`/kb/${kbId}/ask`} className={cn(buttonClass("primary", "lg"), "mt-6")}>
          Ask your first question <ArrowRight className="size-5" aria-hidden />
        </Link>
        <div className="mt-8 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <Stat label="Documents" value={formatNumber(Number(summary.documents ?? 0))} />
          <Stat label="Pages read" value={formatNumber(Number(summary.pages ?? 0))} />
          <Stat label="Passages" value={formatNumber(Number(summary.total_chunks ?? 0))} />
          <Stat label="Time taken" value={formatMs(duration(job.started_at, job.finished_at))} />
        </div>
      </section>
    );
  }
  if (job.status === "failed") {
    return (
      <section role="alert" className="rounded-xl bg-bad-soft p-6 md:p-8">
        <h2 className="text-lg font-semibold text-bad">The build stopped</h2>
        <p className="mt-2 text-sm text-ink">{job.error ?? "Something went wrong."}</p>
        <Link to={`/kb/${kbId}/setup`} className={cn(buttonClass("secondary"), "mt-5")}>
          Check the settings
        </Link>
      </section>
    );
  }
  if (job.status === "cancelled") {
    return <p className="text-sm text-slate">This build was cancelled. Chat still uses the previous version.</p>;
  }
  const done = job.stages.filter((stage) => stage.status !== "pending" && stage.status !== "running").length;
  const reading = job.stages.find((stage) => stage.stage === "parse");
  return (
    <section aria-live="polite" className="rounded-xl border border-rule bg-panel p-6 md:p-8">
      <h2 className="text-lg font-semibold">Archie is reading your documents</h2>
      <p className="mt-1 text-sm text-slate">
        {reading && reading.items_in > 0
          ? `${reading.items_out + reading.items_failed} of ${reading.items_in} documents read.`
          : "Getting started."}{" "}
        You can leave this page; chat keeps using the previous version until this finishes.
      </p>
      <div
        className="mt-5 h-1.5 overflow-hidden rounded-full bg-plinth"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={job.stages.length}
        aria-valuenow={done}
        aria-label="Build progress"
      >
        <div
          className="h-full bg-accent transition-[width] duration-500"
          style={{ width: `${(done / Math.max(1, job.stages.length)) * 100}%` }}
        />
      </div>
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
      navigate(`/kb/${kbId}/builds/${job_id}`);
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
              Cancel build
            </Button>
          ) : (
            <Button busy={start.isPending} onClick={() => start.mutate()}>
              Build again
            </Button>
          )
        }
      />
      <div className="grid gap-10 px-5 pb-16 md:px-10 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-w-0 space-y-8">
          <ErrorNote error={start.error ?? error} />
          {builds.isLoading && <Skeleton />}
          {!builds.isLoading && !jobId && (
            <EmptyState title="Not built yet">
              Check the documents and settings in{" "}
              <Link to={`/kb/${kbId}/setup`} className="font-semibold text-accent underline underline-offset-4">
                Setup
              </Link>
              , then build. It usually takes a few minutes.
            </EmptyState>
          )}
          {job && (
            <>
              <Outcome job={job} kbId={kbId} />
              {job.failures.length > 0 && (
                <section>
                  <h2 className="text-base font-semibold">Documents that couldn't be read</h2>
                  <ul className="mt-2 divide-y divide-rule border-y border-rule">
                    {job.failures.map((failure) => (
                      <li key={failure.document + failure.stage} className="py-3">
                        <div className="font-semibold">{failure.document}</div>
                        <div className="text-sm text-slate">{failure.error}</div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <section>
                <h2 className="text-base font-semibold">Steps</h2>
                <ol className="mt-1 divide-y divide-rule border-y border-rule">
                  {job.stages.map((stage) => (
                    <StageRow key={stage.stage} stage={stage} />
                  ))}
                </ol>
              </section>
            </>
          )}
        </div>
        <aside aria-label="Earlier builds">
          <h2 className="text-base font-semibold">Earlier builds</h2>
          <ul className="mt-2 divide-y divide-rule border-y border-rule">
            {builds.data?.map((item) => (
              <li key={item.id}>
                <Link
                  to={`/kb/${kbId}/builds/${item.id}`}
                  aria-current={item.id === jobId ? "page" : undefined}
                  className={cn(
                    "flex items-center justify-between gap-2 px-2 py-3 hover:bg-plinth",
                    item.id === jobId && "bg-plinth",
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
