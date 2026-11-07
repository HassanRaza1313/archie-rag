import { useQuery } from "@tanstack/react-query";
import { FileSearch, MessageSquareQuote, ScanSearch } from "lucide-react";
import type { ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";
import { buttonClass, cn, ErrorNote, Skeleton } from "../../components/ui";
import { api, type KnowledgeBase } from "../../lib/api";

/** The chatbot to open: the one used last, if it still exists, else the first. */
export function openingChatbot(kbs: KnowledgeBase[], last: string | null): KnowledgeBase | undefined {
  return kbs.find((kb) => kb.id === last) ?? kbs[0];
}

function lastOpened(): string | null {
  try {
    return localStorage.getItem("archie.last");
  } catch {
    return null;
  }
}

function Feature({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-4">
      <span className="mt-0.5 text-accent [&>svg]:size-7 [&>svg]:stroke-[1.6]">{icon}</span>
      <span>
        <span className="block text-base font-semibold">{title}</span>
        <span className="block text-base text-slate">{children}</span>
      </span>
    </li>
  );
}

function Welcome() {
  return (
    <div className="flex min-h-full items-center justify-center bg-wall px-5 py-12">
      <div className="settle w-full max-w-md text-center">
        <svg viewBox="0 0 24 24" className="mx-auto size-16 drop-shadow-sm" aria-hidden>
          <rect width="24" height="24" rx="6" className="fill-accent" />
          <path
            d="M7.2 17 12 6.5 16.8 17M8.9 13.3h6.2"
            fill="none"
            stroke="white"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <h1 className="mt-6 font-display text-[2rem] leading-tight font-bold tracking-tight">Welcome to Archie</h1>
        <ul className="mt-9 space-y-6 text-left">
          <Feature icon={<FileSearch />} title="Reads your documents">
            PDFs, Word, PowerPoint, Excel and more, from a folder or uploaded.
          </Feature>
          <Feature icon={<MessageSquareQuote />} title="Answers with sources">
            Every answer shows the passages it came from, or says when it doesn't know.
          </Feature>
          <Feature icon={<ScanSearch />} title="Shows its work">
            Open any answer to see what Archie searched and why it chose each passage.
          </Feature>
        </ul>
        <Link to="/new" className={cn(buttonClass("primary", "lg"), "mt-11 w-full")}>
          Continue
        </Link>
      </div>
    </div>
  );
}

export function HomePage() {
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: api.listKbs });
  if (kbs.isLoading) {
    return (
      <div className="px-8 py-10">
        <Skeleton lines={3} />
      </div>
    );
  }
  if (kbs.error) {
    return (
      <div className="px-8 py-10">
        <ErrorNote error={kbs.error} />
      </div>
    );
  }
  const target = openingChatbot(kbs.data ?? [], lastOpened());
  return target ? <Navigate to={`/kb/${target.id}/ask`} replace /> : <Welcome />;
}
