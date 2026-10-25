import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus, Trash2, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, cn, ErrorNote, Input, PageHeader, Select, Stat, TextArea } from "../../components/ui";
import { api, type EvalReport } from "../../lib/api";
import { formatMs, formatPercent, relativeTime } from "../../lib/format";

type Row = { id: string; question: string; expected: string };
type Run = EvalReport & { runId: number; ranAt: number };

const SAMPLE: Row[] = [
  {
    id: crypto.randomUUID(),
    question: "What ratio of browns to greens should a compost pile have?",
    expected: "home-composting-guide.md",
  },
  {
    id: crypto.randomUUID(),
    question: "How many days of paid leave do employees get?",
    expected: "remote-team-handbook.md",
  },
  { id: crypto.randomUUID(), question: "Who won the 1998 football world cup?", expected: "" },
];

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

function loadRuns(kbId: string): Run[] {
  try {
    return JSON.parse(localStorage.getItem(`archie.eval.${kbId}`) ?? "[]") as Run[];
  } catch {
    return [];
  }
}

function best(runs: Run[], metric: (typeof METRICS)[number]): number | null {
  const values = runs.map((run) => run.metrics[metric.key]).filter((v): v is number => typeof v === "number");
  if (values.length < 2) return null;
  return metric.higherIsBetter ? Math.max(...values) : Math.min(...values);
}

export function EvalPage() {
  const { kbId = "" } = useParams();
  const versions = useQuery({ queryKey: ["versions", kbId], queryFn: () => api.versions(kbId) });
  const [rows, setRows] = useState<Row[]>(SAMPLE);
  const [version, setVersion] = useState("");
  const [runs, setRuns] = useState<Run[]>(() => loadRuns(kbId));
  const [fileError, setFileError] = useState<string | null>(null);
  useEffect(() => {
    try {
      localStorage.setItem(`archie.eval.${kbId}`, JSON.stringify(runs));
    } catch {
      /* storage unavailable */
    }
  }, [runs, kbId]);

  const filled = rows.filter((row) => row.question.trim());
  const run = useMutation({
    mutationFn: () =>
      api.evaluate(
        kbId,
        filled.map((row) => ({
          question: row.question.trim(),
          expected_sources: row.expected.trim() ? [row.expected.trim()] : [],
        })),
        version ? Number(version) : undefined,
      ),
    onSuccess: (report) =>
      setRuns((current) => [{ ...report, runId: Date.now(), ranAt: Date.now() / 1000 }, ...current].slice(0, 6)),
  });
  const latest = runs[0];
  const update = (id: string, change: Partial<Row>) =>
    setRows((current) => current.map((row) => (row.id === id ? { ...row, ...change } : row)));

  return (
    <>
      <PageHeader
        title="Evaluate"
        description="Check whether Archie finds the right document for questions you know the answer to. Compare settings versions before you keep a change."
      />
      <div className="grid gap-12 px-5 pb-16 md:px-10 xl:grid-cols-[minmax(0,30rem)_minmax(0,1fr)]">
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
                      (parsed.items as { question: string; expected_sources?: string[] }[]).map((item) => ({
                        id: crypto.randomUUID(),
                        question: item.question,
                        expected: item.expected_sources?.[0] ?? "",
                      })),
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
          <ol className="mt-4 space-y-3">
            {rows.map((row, index) => (
              <li
                key={row.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 rounded-lg border border-rule bg-panel p-3"
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
                  onClick={() => setRows((r) => r.filter((item) => item.id !== row.id))}
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
                  className="col-span-2 font-mono text-xs"
                />
              </li>
            ))}
          </ol>
          <Button
            variant="ghost"
            className="mt-3"
            onClick={() => setRows((r) => [...r, { id: crypto.randomUUID(), question: "", expected: "" }])}
          >
            <Plus className="size-4" aria-hidden /> Add a question
          </Button>
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
            <p className="max-w-md border-t border-rule pt-5 text-sm text-slate">
              Results appear here. Run the same questions against two settings versions to see which finds the right
              documents more often.
            </p>
          )}
          {latest && (
            <>
              <div className="grid grid-cols-2 gap-x-8 gap-y-5 md:grid-cols-3">
                {METRICS.map((metric) => (
                  <Stat
                    key={metric.key}
                    label={metric.label}
                    value={metric.format(latest.metrics[metric.key])}
                    hint={metric.hint}
                  />
                ))}
              </div>
              {latest.warnings.map((warning) => (
                <p key={warning} className="text-sm text-warn">
                  {warning}
                </p>
              ))}
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="mb-2 text-left font-semibold">Each question</caption>
                  <thead className="border-b border-rule text-xs text-slate">
                    <tr>
                      <th className="py-2 pr-4 font-semibold">Question</th>
                      <th className="py-2 font-semibold">Result</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-rule">
                    {latest.results.map((row) => {
                      const good = row.answerable
                        ? row.rank !== null && row.rank <= 5 && !row.abstained
                        : row.abstained;
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
                          <td className={cn("py-3 font-semibold", good ? "text-ok" : "text-bad")}>{text}</td>
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
                    <tr key={report.runId}>
                      <td className="py-3 pr-4 font-sans">
                        Version {report.version}
                        <span className="block text-xs text-slate">{relativeTime(report.ranAt)}</span>
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
