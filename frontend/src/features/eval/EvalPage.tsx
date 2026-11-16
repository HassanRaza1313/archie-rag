import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Sparkles, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, cn, ErrorNote, Group, Input, PageHeader, Row, Select, TextArea } from "../../components/ui";
import { api, type EvalReport, type EvalRun, type GoldenItem } from "../../lib/api";
import { formatMs, formatPercent, relativeTime } from "../../lib/format";

type QuestionRow = { id: string; question: string; expected: string; drafted?: boolean };
type Result = EvalReport["results"][number];

const toRow = (item: GoldenItem, drafted = false): QuestionRow => ({
  id: crypto.randomUUID(),
  question: item.question,
  expected: item.expected_sources[0] ?? "",
  drafted,
});

const toItem = (row: QuestionRow): GoldenItem => ({
  question: row.question.trim(),
  expected_sources: row.expected.trim() ? [row.expected.trim()] : [],
});

/** Whether a question got the outcome it should: the expected document in the top 5, or a decline. */
export function passed(result: Result): boolean {
  return result.answerable ? result.rank !== null && result.rank <= 5 && !result.abstained : result.abstained;
}

/** Per question, "better" or "worse" against the previous run; unchanged and new questions are left out. */
export function changes(latest: EvalReport, previous: EvalReport | undefined): Map<string, "better" | "worse"> {
  const before = new Map((previous?.results ?? []).map((result) => [result.question, passed(result)]));
  const changed = new Map<string, "better" | "worse">();
  for (const result of latest.results) {
    const was = before.get(result.question);
    if (was !== undefined && was !== passed(result)) changed.set(result.question, was ? "worse" : "better");
  }
  return changed;
}

type MetricKey = keyof EvalReport["metrics"] & string;
const METRICS: {
  key: MetricKey;
  label: string;
  hint: string;
  higherIsBetter: boolean;
  format: (v: number | null | undefined) => string;
}[] = [
  {
    key: "hit_at_1",
    label: "Right document first",
    hint: "Its top passage came from the expected document",
    higherIsBetter: true,
    format: (v) => formatPercent(v),
  },
  {
    key: "hit_at_5",
    label: "Right document in top 5",
    hint: "Found among its five best passages",
    higherIsBetter: true,
    format: (v) => formatPercent(v),
  },
  {
    key: "mrr",
    label: "Ranking score",
    hint: "1.0 means always ranked first (MRR)",
    higherIsBetter: true,
    format: (v) => (v === null || v === undefined ? "–" : v.toFixed(2)),
  },
  {
    key: "false_abstain_rate",
    label: "Wrongly declined",
    hint: "Declined questions it should answer",
    higherIsBetter: false,
    format: (v) => formatPercent(v),
  },
  {
    key: "correct_abstain_rate",
    label: "Correctly declined",
    hint: "Declined questions outside the documents",
    higherIsBetter: true,
    format: (v) => formatPercent(v),
  },
  {
    key: "latency_p50_ms",
    label: "Typical search time",
    hint: "Half of searches were faster",
    higherIsBetter: false,
    format: (v) => formatMs(v),
  },
];

/** Parse a JSONL golden set; returns items or a readable error naming the first bad line. */
export function parseGolden(text: string): { items: unknown[]; error?: string } {
  const items: unknown[] = [];
  const lines = text.split("\n");
  for (const [index, line] of lines.entries()) {
    if (!line.trim()) continue;
    try {
      const item = JSON.parse(line);
      if (typeof item?.question !== "string") return { items, error: `line ${index + 1}: missing "question"` };
      items.push(item);
    } catch {
      return { items, error: `line ${index + 1}: not valid JSON` };
    }
  }
  return items.length ? { items } : { items, error: "add at least one question" };
}

function best(runs: EvalRun[], metric: (typeof METRICS)[number]): number | null {
  const values = runs.map((run) => run.metrics[metric.key]).filter((v): v is number => typeof v === "number");
  if (values.length < 2) return null;
  return metric.higherIsBetter ? Math.max(...values) : Math.min(...values);
}

export function EvalPage() {
  const { kbId = "" } = useParams();
  const queryClient = useQueryClient();
  const versions = useQuery({ queryKey: ["versions", kbId], queryFn: () => api.versions(kbId) });
  const golden = useQuery({ queryKey: ["golden", kbId], queryFn: () => api.golden(kbId) });
  const history = useQuery({
    queryKey: ["eval-runs", kbId],
    queryFn: () => api.evalRuns(kbId),
    refetchInterval: 15_000,
  });
  const [rows, setRows] = useState<QuestionRow[] | null>(null);
  const [version, setVersion] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const loadedFor = useRef<string | null>(null);
  useEffect(() => {
    if (golden.data && loadedFor.current !== kbId) {
      loadedFor.current = kbId;
      setRows(golden.data.items.map((item) => toRow(item)));
    }
  }, [golden.data, kbId]);

  const filled = (rows ?? []).filter((row) => row.question.trim());
  const save = useMutation({ mutationFn: (items: GoldenItem[]) => api.saveGolden(kbId, items) });
  const serialized = JSON.stringify(filled.map(toItem));
  const lastSaved = useRef<string | null>(null);
  useEffect(() => {
    if (rows === null) return;
    if (lastSaved.current === null) {
      lastSaved.current = serialized; // what was just loaded is already saved
      return;
    }
    if (serialized === lastSaved.current) return;
    const timer = setTimeout(() => {
      lastSaved.current = serialized;
      save.mutate(JSON.parse(serialized) as GoldenItem[]);
    }, 700);
    return () => clearTimeout(timer);
  }, [serialized, rows, save.mutate]);

  const draft = useMutation({
    mutationFn: () => api.draftGolden(kbId),
    onSuccess: ({ items }) => {
      const known = new Set(filled.map((row) => row.question.trim().toLowerCase()));
      const fresh = items.filter((item) => !known.has(item.question.toLowerCase())).map((item) => toRow(item, true));
      setRows((current) => [...(current ?? []).filter((row) => row.question.trim()), ...fresh]);
    },
  });
  const run = useMutation({
    mutationFn: () => api.evaluate(kbId, filled.map(toItem), version ? Number(version) : undefined),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["eval-runs", kbId] }),
  });
  const runs = history.data ?? [];
  const latest = runs[0];
  const changed = latest ? changes(latest, runs[1]) : new Map<string, "better" | "worse">();
  const update = (id: string, change: Partial<QuestionRow>) =>
    setRows((current) => (current ?? []).map((row) => (row.id === id ? { ...row, ...change, drafted: false } : row)));

  return (
    <>
      <PageHeader
        title="Quality"
        description="Check whether Archie finds the right document for questions you know the answer to. Your questions are kept and run again after every rebuild or settings change, so you can see what got better or worse."
      />
      <div className="grid gap-12 px-5 pb-16 md:px-8 xl:grid-cols-[minmax(0,30rem)_minmax(0,1fr)]">
        <section aria-labelledby="questions">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="questions" className="text-lg font-semibold">
              Test questions
            </h2>
            <label className="inline-flex cursor-pointer items-center gap-1.5 text-sm font-semibold text-accent">
              <Upload className="size-4" aria-hidden /> Import file
              <input
                type="file"
                accept=".jsonl,.json,.txt"
                className="sr-only"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  const parsed = parseGolden(await file.text());
                  setFileError(parsed.error ?? null);
                  if (!parsed.error) {
                    setRows(
                      (parsed.items as { question: string; expected_sources?: string[] }[]).map((item) =>
                        toRow({ question: item.question, expected_sources: item.expected_sources ?? [] }),
                      ),
                    );
                  }
                }}
              />
            </label>
          </div>
          <p className="mt-1 text-sm text-slate">
            Name the document each answer should come from. Leave it empty for questions the documents can't answer, so
            Archie should decline.
          </p>
          {fileError && <p className="mt-2 text-sm font-semibold text-bad">That file couldn't be read: {fileError}.</p>}
          <ErrorNote error={golden.error} />
          {rows !== null && rows.length === 0 && (
            <div className="mt-4 rounded-[var(--radius-group)] bg-panel px-5 py-5 shadow-[0_0_0_0.5px_var(--color-rule)]">
              <p className="font-semibold">No test questions yet</p>
              <p className="mt-1 text-sm text-slate">
                Archie can draft some from your documents: one per passage it picks across your files, plus a couple it
                should decline. Check each one, then keep, edit or remove it.
              </p>
            </div>
          )}
          <ol className="mt-4 space-y-3">
            {(rows ?? []).map((row, index) => (
              <li
                key={row.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)] p-3"
              >
                <label className="sr-only" htmlFor={`q-${index}`}>
                  Question {index + 1}
                </label>
                <TextArea
                  id={`q-${index}`}
                  rows={2}
                  className="resize-none"
                  value={row.question}
                  placeholder="Question"
                  onChange={(e) => update(row.id, { question: e.target.value })}
                />
                <Button
                  variant="ghost"
                  aria-label={`Remove question ${index + 1}`}
                  onClick={() => setRows((r) => (r ?? []).filter((item) => item.id !== row.id))}
                >
                  <Trash2 className="size-4" />
                </Button>
                <label className="sr-only" htmlFor={`d-${index}`}>
                  Expected document for question {index + 1}
                </label>
                <Input
                  id={`d-${index}`}
                  value={row.expected}
                  placeholder="File it should come from, or leave empty if Archie should decline"
                  onChange={(e) => update(row.id, { expected: e.target.value })}
                  className={cn("font-mono text-xs", row.drafted ? "col-span-1" : "col-span-2")}
                />
                {row.drafted && (
                  <span className="self-center justify-self-end">
                    <Badge tone="neutral" title="Drafted from your documents: check it before relying on it">
                      Drafted
                    </Badge>
                  </span>
                )}
              </li>
            ))}
          </ol>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="ghost" busy={draft.isPending} onClick={() => draft.mutate()}>
              <Sparkles className="size-4" aria-hidden /> Draft questions from your documents
            </Button>
            <Button
              variant="ghost"
              onClick={() => setRows((r) => [...(r ?? []), toRow({ question: "", expected_sources: [] })])}
            >
              <Plus className="size-4" aria-hidden /> Add a question
            </Button>
            <span role="status" className="ml-auto text-xs text-slate">
              {save.isPending ? "Saving…" : save.isError ? "Couldn't save" : save.isSuccess ? "Saved" : ""}
            </span>
          </div>
          <ErrorNote error={draft.error} />
          <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-rule pt-5">
            <Select
              aria-label="Settings version to test"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              className="w-60"
            >
              <option value="">Current settings</option>
              {versions.data?.map((item) => (
                <option key={item.version} value={item.version}>
                  Version {item.version} · {relativeTime(item.created_at)}
                </option>
              ))}
            </Select>
            <Button variant="primary" busy={run.isPending} disabled={!filled.length} onClick={() => run.mutate()}>
              Run {filled.length} question{filled.length === 1 ? "" : "s"}
            </Button>
          </div>
          <div className="mt-3">
            <ErrorNote error={run.error} />
          </div>
        </section>

        <section aria-labelledby="results" className="min-w-0 space-y-10">
          <h2 id="results" className="sr-only">
            Results
          </h2>
          {!latest && (
            <p className="max-w-md text-sm text-slate">
              Results appear here. After that, every rebuild and settings change runs your questions again and marks
              what got better or worse.
            </p>
          )}
          {latest && (
            <>
              <p className="text-sm text-slate">
                Latest run: version {latest.version}, {latest.reason}, {relativeTime(latest.ran_at)}.
                {changed.size > 0 &&
                  ` Since the run before: ${[...changed.values()].filter((c) => c === "better").length} better, ${
                    [...changed.values()].filter((c) => c === "worse").length
                  } worse.`}
              </p>
              <Group title="Results">
                {METRICS.map((metric) => (
                  <Row key={metric.key} label={metric.label} detail={metric.hint}>
                    <span className="font-medium text-ink tabular-nums">
                      {metric.format(latest.metrics[metric.key])}
                    </span>
                  </Row>
                ))}
              </Group>
              {latest.warnings.map((warning) => (
                <p key={warning} className="text-sm text-warn">
                  {warning}
                </p>
              ))}
              <div className="overflow-x-auto rounded-[var(--radius-group)] bg-panel px-4 shadow-[0_0_0_0.5px_var(--color-rule)]">
                <table className="w-full text-left text-sm">
                  <caption className="pt-3 pb-1 text-left text-sm font-semibold text-slate">Each question</caption>
                  <thead className="border-b border-rule text-xs text-slate">
                    <tr>
                      <th className="py-2 pr-4 font-semibold">Question</th>
                      <th className="py-2 font-semibold">Result</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-rule">
                    {latest.results.map((row) => {
                      const good = passed(row);
                      const change = changed.get(row.question);
                      const text = row.answerable
                        ? row.abstained
                          ? "Declined, but should have answered"
                          : row.rank
                            ? `Found it at position ${row.rank}`
                            : "Didn't find the expected document"
                        : row.abstained
                          ? "Correctly declined"
                          : "Answered, but should have declined";
                      return (
                        <tr key={row.question}>
                          <td className="py-3 pr-4">{row.question}</td>
                          <td className={cn("py-3 font-semibold", good ? "text-ok" : "text-bad")}>
                            {text}
                            {change && (
                              <span className="ml-2 align-[1px]">
                                <Badge tone={change === "better" ? "ok" : "bad"}>
                                  {change === "better" ? "Better" : "Worse"}
                                </Badge>
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {runs.length > 1 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="mb-2 text-left font-semibold">Compare runs (best in each column is bold)</caption>
                <thead className="border-b border-rule text-xs text-slate">
                  <tr>
                    <th className="py-2 pr-4 font-semibold">Run</th>
                    {METRICS.map((metric) => (
                      <th key={metric.key} className="py-2 pr-4 text-right font-semibold">
                        {metric.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule font-mono">
                  {runs.map((report) => (
                    <tr key={report.id}>
                      <td className="py-3 pr-4 font-sans">
                        Version {report.version}
                        <span className="block text-xs text-slate">
                          {report.reason} · {relativeTime(report.ran_at)}
                        </span>
                      </td>
                      {METRICS.map((metric) => {
                        const value = report.metrics[metric.key];
                        return (
                          <td
                            key={metric.key}
                            className={cn(
                              "py-3 pr-4 text-right tabular-nums",
                              value === best(runs, metric) && "font-bold text-ok",
                            )}
                          >
                            {metric.format(value)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
