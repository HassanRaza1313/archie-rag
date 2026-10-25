import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus, Settings2, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Button,
  buttonClass,
  cn,
  Dialog,
  ErrorNote,
  GalleryLabel,
  PageHeader,
  Skeleton,
  StatusBadge,
} from "../../components/ui";
import { api, type KnowledgeBase } from "../../lib/api";
import { formatNumber, relativeTime } from "../../lib/format";

/** The one next step a chatbot's state calls for. */
export function nextAction(kb: KnowledgeBase): { label: string; to: string } {
  if (kb.status === "building") return { label: "Watch the build", to: `/kb/${kb.id}/builds` };
  if (kb.status === "stale") return { label: "Rebuild", to: `/kb/${kb.id}/setup` };
  if (kb.status === "failed") return { label: "See what went wrong", to: `/kb/${kb.id}/builds` };
  if (kb.status === "draft") return { label: "Continue setup", to: `/kb/${kb.id}/setup` };
  return { label: "Ask", to: `/kb/${kb.id}/ask` };
}

function describe(kb: KnowledgeBase): string {
  if (kb.status === "draft") return "Choose its documents and build it to start asking.";
  if (kb.status === "building") return "Reading its documents now. You can keep working.";
  const parts = [`${formatNumber(kb.documents)} document${kb.documents === 1 ? "" : "s"}`];
  parts.push(`${formatNumber(kb.chunks)} passages`);
  if (kb.last_job?.finished_at) parts.push(`built ${relativeTime(kb.last_job.finished_at)}`);
  return parts.join(" · ");
}

function ChatbotRow({ kb, onDelete }: { kb: KnowledgeBase; onDelete: () => void }) {
  const action = nextAction(kb);
  return (
    <li className="flex flex-col rounded-[3px] border border-rule bg-panel px-6 pt-5 pb-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={action.to} className="text-lg font-semibold tracking-tight hover:text-accent">
          {kb.name}
        </Link>
        <StatusBadge status={kb.status} />
      </div>
      <p className="mt-1.5 text-sm text-slate">{describe(kb)}</p>
      <div className="mt-6 flex items-center gap-1">
        <Link to={action.to} className={buttonClass(kb.status === "ready" ? "primary" : "secondary")}>
          {action.label} <ArrowRight className="size-4" aria-hidden />
        </Link>
        <Link
          to={`/kb/${kb.id}/setup`}
          aria-label={`Settings for ${kb.name}`}
          className={cn(buttonClass("ghost"), "ml-auto")}
        >
          <Settings2 className="size-4" aria-hidden />
        </Link>
        <Button variant="ghost" aria-label={`Delete ${kb.name}`} onClick={onDelete}>
          <Trash2 className="size-4" />
        </Button>
      </div>
    </li>
  );
}

function Welcome() {
  const navigate = useNavigate();
  return (
    <div className="px-5 pt-14 pb-16 md:px-10 md:pt-20">
      <h1 className="max-w-3xl text-2xl font-semibold tracking-tight text-balance">
        Turn a folder of documents into a chatbot that shows its sources.
      </h1>
      <p className="mt-4 max-w-2xl text-base text-slate">
        Archie reads your documents, answers questions about them, and labels every answer with where it came from. Most
        chatbots are ready in a few minutes.
      </p>
      <Button variant="primary" size="lg" className="mt-8" onClick={() => navigate("/new")}>
        <Plus className="size-5" aria-hidden /> Create your first chatbot
      </Button>
      <ol className="mt-14 grid max-w-4xl gap-4 sm:grid-cols-3">
        <GalleryLabel as="li" title="1. Name it">
          Something people will recognise, like “Team handbook”.
        </GalleryLabel>
        <GalleryLabel as="li" title="2. Choose documents">
          A folder of PDF, Markdown or text files, or upload them.
        </GalleryLabel>
        <GalleryLabel as="li" title="3. Build and ask">
          Archie reads everything once, then answers with sources.
        </GalleryLabel>
      </ol>
    </div>
  );
}

export function HomePage() {
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const kbs = useQuery({
    queryKey: ["kbs"],
    queryFn: api.listKbs,
    refetchInterval: (query) => (query.state.data?.some((kb) => kb.status === "building") ? 2_000 : false),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteKb(id),
    onSuccess: () => {
      setDeleting(null);
      queryClient.invalidateQueries({ queryKey: ["kbs"] });
    },
  });

  if (kbs.isLoading) {
    return (
      <div className="px-5 py-10 md:px-10">
        <Skeleton lines={4} />
      </div>
    );
  }
  if (kbs.data && kbs.data.length === 0) return <Welcome />;

  return (
    <>
      <PageHeader
        title="Your chatbots"
        description="Each chatbot answers from one collection of documents and shows where every answer came from."
        actions={
          <Link to="/new" className={buttonClass("primary")}>
            <Plus className="size-4" aria-hidden /> New chatbot
          </Link>
        }
      />
      <div className="px-5 pb-16 md:px-10">
        <ErrorNote error={kbs.error} />
        <ul className="mt-4 grid gap-6 lg:grid-cols-2">
          {kbs.data?.map((kb) => (
            <ChatbotRow key={kb.id} kb={kb} onDelete={() => setDeleting(kb)} />
          ))}
        </ul>
      </div>
      <Dialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.name ?? "chatbot"}?`}
        footer={
          <>
            <Button onClick={() => setDeleting(null)}>Keep it</Button>
            <Button variant="danger" busy={remove.isPending} onClick={() => deleting && remove.mutate(deleting.id)}>
              Delete chatbot
            </Button>
          </>
        }
      >
        <p className="text-sm">
          Its index, settings history and activity are removed. Your original documents are not touched. This can't be
          undone.
        </p>
        <div className="mt-3">
          <ErrorNote error={remove.error} />
        </div>
      </Dialog>
    </>
  );
}
