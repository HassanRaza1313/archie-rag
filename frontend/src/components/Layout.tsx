import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  FlaskConical,
  Hammer,
  HeartPulse,
  KeyRound,
  LayoutGrid,
  Menu,
  MessageSquareText,
  Moon,
  SlidersHorizontal,
  Sun,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useParams } from "react-router-dom";
import { api, getApiKey, setApiKey } from "../lib/api";
import { Button, cn, Dialog, Input } from "./ui";

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem("archie.theme");
      return saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("archie.theme", dark ? "dark" : "light");
    } catch {
      /* storage unavailable */
    }
  }, [dark]);
  return [dark, () => setDark((value) => !value)];
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <svg viewBox="0 0 28 28" className="size-7" aria-hidden>
        <rect x="1" y="1" width="26" height="26" rx="3" fill="none" stroke="currentColor" strokeWidth="2" />
        <path
          d="M8 20 14 7l6 13M10.4 15h7.2"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinejoin="round"
        />
      </svg>
      <span className="text-lg font-bold tracking-tight">Archie</span>
    </span>
  );
}

function NavItem({ to, icon, children, end }: { to: string; icon: ReactNode; children: ReactNode; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "flex h-10 items-center gap-3 rounded-lg px-3 text-sm transition-colors duration-150",
          isActive
            ? "bg-rail-ink font-semibold text-rail"
            : "text-rail-muted hover:bg-rail-active/60 hover:text-rail-ink",
        )
      }
    >
      <span className="[&>svg]:size-[18px]">{icon}</span>
      {children}
    </NavLink>
  );
}

function ServiceStatus() {
  const ready = useQuery({ queryKey: ["ready"], queryFn: api.ready, refetchInterval: 20_000, retry: false });
  const problems = [
    !ready.isSuccess && "Archie's server",
    ready.data && !ready.data.qdrant && "the search index",
    ready.data && !ready.data.ollama && "the AI model server",
  ].filter(Boolean);
  return (
    <div className="flex items-center gap-2 text-xs text-rail-muted" role="status" aria-live="polite">
      <span className={cn("size-2 rounded-full", problems.length ? "bg-rail-bad" : "bg-rail-ok")} aria-hidden />
      {ready.isLoading
        ? "Checking services…"
        : problems.length
          ? `Can't reach ${problems.join(", ")}`
          : "All services running"}
    </div>
  );
}

function ApiKeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [value, setValue] = useState(getApiKey);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Access key"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              setApiKey(value.trim());
              onClose();
              window.location.reload();
            }}
          >
            Save key
          </Button>
        </>
      }
    >
      <label htmlFor="api-key" className="text-sm text-slate">
        Only needed when whoever runs Archie has protected it with a key. It is stored in this browser.
      </label>
      <Input
        id="api-key"
        data-autofocus
        type="password"
        className="mt-3"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
    </Dialog>
  );
}

const SECTIONS = [
  { path: "ask", label: "Ask", icon: <MessageSquareText /> },
  { path: "setup", label: "Setup", icon: <SlidersHorizontal /> },
  { path: "builds", label: "Builds", icon: <Hammer /> },
  { path: "evaluate", label: "Evaluate", icon: <FlaskConical /> },
  { path: "activity", label: "Activity", icon: <Activity /> },
];

export function Layout() {
  const { kbId } = useParams();
  const location = useLocation();
  const [dark, toggleTheme] = useTheme();
  const [keyOpen, setKeyOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 768px)").matches);
  const kb = useQuery({ queryKey: ["kb", kbId], queryFn: () => api.getKb(kbId as string), enabled: !!kbId });

  useEffect(() => {
    const query = window.matchMedia("(min-width: 768px)");
    const update = () => setDesktop(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: close the phone menu after every navigation
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);
  const railHidden = !desktop && !menuOpen;

  return (
    <div className="flex h-full">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-panel focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      {menuOpen && !desktop && (
        <button
          type="button"
          aria-label="Close menu"
          className="fixed inset-0 z-30 bg-black/40"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        inert={railHidden || undefined}
        aria-hidden={railHidden || undefined}
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col bg-rail text-rail-ink transition-transform duration-200",
          "md:static md:translate-x-0",
          railHidden ? "-translate-x-full" : "translate-x-0",
        )}
      >
        <div className="px-5 pt-6 pb-7">
          <Wordmark />
        </div>
        <nav aria-label="Main" className="flex flex-col gap-1 px-3">
          <NavItem to="/" end icon={<LayoutGrid />}>
            Chatbots
          </NavItem>
          <NavItem to="/health" icon={<HeartPulse />}>
            Health
          </NavItem>
        </nav>
        {kbId && (
          <nav aria-label={kb.data?.name ?? "Chatbot"} className="mt-7 flex flex-col gap-1 px-3">
            <div className="truncate px-3 pb-2 text-xs font-semibold text-rail-muted">{kb.data?.name ?? "Chatbot"}</div>
            {SECTIONS.map((section) => (
              <NavItem key={section.path} to={`/kb/${kbId}/${section.path}`} icon={section.icon}>
                {section.label}
              </NavItem>
            ))}
          </nav>
        )}
        <div className="mt-auto space-y-3 border-t border-white/15 px-5 py-4">
          <ServiceStatus />
          <div className="flex gap-1">
            <button
              type="button"
              onClick={toggleTheme}
              className="flex h-9 items-center gap-2 rounded-lg px-2 text-xs text-rail-muted hover:bg-rail-active hover:text-rail-ink"
            >
              {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
              {dark ? "Light" : "Dark"}
            </button>
            <button
              type="button"
              onClick={() => setKeyOpen(true)}
              className="flex h-9 items-center gap-2 rounded-lg px-2 text-xs text-rail-muted hover:bg-rail-active hover:text-rail-ink"
            >
              <KeyRound className="size-4" aria-hidden />
              Access key
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-rule bg-panel px-4 md:hidden">
          <Button
            variant="ghost"
            size="sm"
            aria-label="Open menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <Menu className="size-5" />
          </Button>
          <Wordmark />
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      {keyOpen && <ApiKeyDialog open onClose={() => setKeyOpen(false)} />}
    </div>
  );
}
