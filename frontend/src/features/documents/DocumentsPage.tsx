import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, EyeOff, Pencil, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  Badge,
  Button,
  buttonClass,
  cn,
  EmptyState,
  ErrorNote,
  PageHeader,
  Skeleton,
  SourceCard,
  TextArea,
} from "../../components/ui";
import { ApiError, api, type DocumentChunk, type IndexedDocument } from "../../lib/api";
import { pages, relativeTime } from "../../lib/format";

export const READERS: Record<string, string> = { fast: "Text reader", layout: "Layout reader" };

export const TEMPLATES: Record<string, string> = {
  structure: "By headings",
  sections: "One per section",
  slides: "One per slide",
  rows: "Groups of table rows",
  paper: "By headings, without references",
  fixed: "Equal pieces",
};

/** How well a document was read, in words; low scores are the files worth opening first. */
export function qualityVerdict(quality: number | undefined): { tone: "ok" | "warn" | "bad"; text: string } {
  if (quality === undefined) return { tone: "warn", text: "Not scored" };
  if (quality >= 0.85) return { tone: "ok", text: "Read well" };
  if (quality >= 0.6) return { tone: "warn", text: "Worth a look" };
  return { tone: "bad", text: "Needs checking" };
}

function problems(document: IndexedDocument): string[] {
  const report = document.report;
  const notes = [...(report.warnings ?? [])];
  if (report.layout_hints?.length && report.reader === "fast")
    notes.unshift(`Complex layout (${report.layout_hints.join(", ")}) read with the text reader`);
  if (report.duplicates) notes.push(`${report.duplicates} passages repeated another file and were skipped`);
  return notes;
}

function DocumentList({ kbId }: { kbId: string }) {
  const documents = useQuery({ queryKey: ["documents", kbId], queryFn: () => api.documents(kbId) });
  const notBuilt = documents.error instanceof ApiError && documents.error.status === 409;
  const sorted = [...(documents.data ?? [])].sort(
    (a, b) => (a.report.quality ?? 0) - (b.report.quality ?? 0) || a.uri.localeCompare(b.uri),
  );
  return (
    <>
      <PageHeader
        title="Documents"
        description="How Archie read each file and split it into passages. Files that may have been misread come first; open one to check its passages and fix any by hand."
      />
      <div className="px-5 pb-16 md:px-8">
        {!notBuilt && <ErrorNote error={documents.error} />}
        {documents.isLoading && <Skeleton lines={5} />}
        {notBuilt && (
          <EmptyState title="Nothing read yet">
            Build this chatbot and every document it reads will be listed here.
          </EmptyState>
        )}
        {documents.data?.length === 0 && <EmptyState title="No documents">The last build indexed no files.</EmptyState>}
        {sorted.length > 0 && (
          <div className="overflow-x-auto rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)] px-4">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-rule text-xs text-slate">
                <tr>
                  <th className="py-2 pr-4 font-semibold">Document</th>
                  <th className="py-2 pr-4 font-semibold">Reading</th>
                  <th className="py-2 pr-4 font-semibold">Read by</th>
                  <th className="py-2 pr-4 font-semibold">Split</th>
                  <th className="py-2 pr-4 text-right font-semibold">Passages</th>
                  <th className="py-2 font-semibold">Read</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {sorted.map((document) => {
                  const verdict = qualityVerdict(document.report.quality);
                  const notes = problems(document);
                  return (
                    <tr key={document.uri} className="align-top hover:bg-plinth">
                      <td className="max-w-md py-3 pr-4">
                        <Link
                          to={`?uri=${encodeURIComponent(document.uri)}`}
                          className="font-semibold break-all hover:text-accent hover:underline"
                        >
                          {document.uri}
                        </Link>
                        {notes.length > 0 && <p className="mt-1 text-xs text-slate">{notes.join(" · ")}</p>}
                      </td>
                      <td className="py-3 pr-4">
                        <Badge tone={verdict.tone}>{verdict.text}</Badge>
                      </td>
                      <td className="py-3 pr-4 text-slate">{READERS[document.report.reader ?? ""] ?? "–"}</td>
                      <td className="py-3 pr-4 text-slate">{TEMPLATES[document.report.template ?? ""] ?? "–"}</td>
                      <td className="py-3 pr-4 text-right font-mono tabular-nums">{document.chunk_count}</td>
                      <td className="py-3 text-slate">{relativeTime(document.indexed_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function ChunkCard({ kbId, uri, chunk }: { kbId: string; uri: string; chunk: DocumentChunk }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["chunks", kbId, uri] });
  const edit = useMutation({
    mutationFn: (change: { text?: string; excluded: boolean }) =>
      api.editChunk(kbId, { uri, ordinal: chunk.ordinal, ...change }),
    onSuccess: () => {
      setDraft(null);
      refresh();
    },
  });
  const restore = useMutation({ mutationFn: () => api.restoreChunk(kbId, uri, chunk.ordinal), onSuccess: refresh });
  const excluded = !!chunk.excluded;
  const mark = [
    `#${chunk.ordinal + 1}`,
    chunk.content_type === "table" ? "Table" : null,
    pages(chunk.page_start, chunk.page_end),
    excluded ? "Left out of search" : chunk.edited ? "Edited" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li>
      <SourceCard mark={mark} title={chunk.section || "No section"} className={cn(excluded && "opacity-60")}>
        {draft === null ? (
          <p
            className={cn(
              "mt-1 text-sm leading-relaxed whitespace-pre-wrap text-ink",
              chunk.content_type === "table" && "overflow-x-auto font-mono text-xs",
            )}
          >
            {chunk.text}
          </p>
        ) : (
          <form
            className="mt-2 space-y-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (draft.trim()) edit.mutate({ text: draft.trim(), excluded });
            }}
          >
            <label htmlFor={`chunk-${chunk.ordinal}`} className="sr-only">
              Passage {chunk.ordinal + 1} text
            </label>
            <TextArea
              id={`chunk-${chunk.ordinal}`}
              data-autofocus
              rows={Math.min(14, Math.max(4, Math.ceil(draft.length / 90)))}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="text-sm"
            />
            <div className="flex gap-2">
              <Button type="submit" size="sm" variant="primary" disabled={!draft.trim() || edit.isPending}>
                {edit.isPending ? "Saving…" : "Save passage"}
              </Button>
              <Button size="sm" onClick={() => setDraft(null)}>
                Cancel
              </Button>
            </div>
          </form>
        )}
        {chunk.enrichment && (
          <p className="mt-2 text-xs text-slate">
            Search hints:{" "}
            {[chunk.enrichment.context, ...chunk.enrichment.questions, ...chunk.enrichment.keywords]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
        <ErrorNote error={edit.error ?? restore.error} />
        {draft === null && (
          <div className="mt-3 flex flex-wrap gap-1">
            <Button size="sm" variant="ghost" onClick={() => setDraft(chunk.text)}>
              <Pencil className="size-3.5" aria-hidden /> Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={() => edit.mutate({ excluded: !excluded })}>
              <EyeOff className="size-3.5" aria-hidden /> {excluded ? "Use in search" : "Leave out of search"}
            </Button>
            {(chunk.edited || excluded) && (
              <Button size="sm" variant="ghost" onClick={() => restore.mutate()} disabled={restore.isPending}>
                <RotateCcw className="size-3.5" aria-hidden /> Undo changes
              </Button>
            )}
          </div>
        )}
      </SourceCard>
    </li>
  );
}

function DocumentDetail({ kbId, uri }: { kbId: string; uri: string }) {
  const detail = useQuery({ queryKey: ["chunks", kbId, uri], queryFn: () => api.documentChunks(kbId, uri) });
  const documents = useQuery({ queryKey: ["documents", kbId], queryFn: () => api.documents(kbId) });
  const document = documents.data?.find((item) => item.uri === uri);
  const verdict = qualityVerdict(document?.report.quality);
  const notes = document ? problems(document) : [];
  return (
    <>
      <PageHeader
        title={uri}
        description={
          document
            ? [
                READERS[document.report.reader ?? ""],
                TEMPLATES[document.report.template ?? ""],
                document.report.pages ? `${document.report.pages} pages` : null,
                `${document.chunk_count} passages`,
              ]
                .filter(Boolean)
                .join(" · ")
            : undefined
        }
        actions={
          <Link to="." className={buttonClass("secondary")}>
            <ArrowLeft className="size-4" aria-hidden /> All documents
          </Link>
        }
      />
      <div className="space-y-6 px-5 pb-16 md:px-8">
        {document && (
          <div className="flex flex-wrap items-center gap-3 text-sm text-slate">
            <Badge tone={verdict.tone}>{verdict.text}</Badge>
            {notes.map((note) => (
              <span key={note}>{note}</span>
            ))}
          </div>
        )}
        <p className="max-w-3xl text-sm text-slate">
          Edits are searched straight away and kept when the chatbot is rebuilt, until the file itself changes.
        </p>
        <ErrorNote error={detail.error} />
        {detail.isLoading && <Skeleton lines={6} />}
        {detail.data && (
          <ol className="grid max-w-4xl gap-3">
            {detail.data.chunks.map((chunk) => (
              <ChunkCard key={chunk.id} kbId={kbId} uri={uri} chunk={chunk} />
            ))}
          </ol>
        )}
      </div>
    </>
  );
}

export function DocumentsPage() {
  const { kbId = "" } = useParams();
  const [params] = useSearchParams();
  const uri = params.get("uri");
  return uri ? <DocumentDetail kbId={kbId} uri={uri} /> : <DocumentList kbId={kbId} />;
}
