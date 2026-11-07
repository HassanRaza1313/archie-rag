import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  FileText,
  Gauge,
  Hammer,
  HeartPulse,
  Loader2,
  Menu,
  Plus,
  Settings,
  SlidersHorizontal,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, getApiKey, type KnowledgeBase, setApiKey } from "../lib/api";
import { Button, cn, Dialog, Group, Input, Row, Segmented, StatusDot } from "./ui";

type Theme = "system" | "light" | "dark";

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => (readStorage("archie.theme") as Theme | null) ?? "system");
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.classList.toggle("dark", theme === "dark" || (theme === "system" && query.matches));
    apply();
    writeStorage("archie.theme", theme);
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [theme]);
  return [theme, setTheme];
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2">
      <svg viewBox="0 0 24 24" className="size-6" aria-hidden>
        <rect width="24" height="24" rx="6" className="fill-accent" />
        <path
          d="M7.2 17 12 6.5 16.8 17M8.9 13.3h6.2"
          fill="none"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span className="text-base font-semibold tracking-tight">Archie</span>
    </span>
  );
}

/** A chatbot's initial in a small rounded tile, as Mac apps show accounts and folders. */
function Tile({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="grid size-6 shrink-0 place-items-center rounded-[6px] bg-accent-soft text-xs font-semibold text-accent"
    >
      {name.trim().charAt(0).toUpperCase() || "A"}
    </span>
  );
}

const FINISHED = new Set(["completed", "completed_with_errors", "failed"]);

/** The last finished build the person has seen, per chatbot: a newer one keeps a dot lit until they look. */
export function unseenBuild(kb: KnowledgeBase, seen: string | null): boolean {
  return !!kb.last_job && FINISHED.has(kb.last_job.status) && kb.last_job.id !== seen;
}

function ChatbotRow({ kb, active }: { kb: KnowledgeBase; active: boolean }) {
  const building = kb.status === "building";
  const unseen = !active && unseenBuild(kb, readStorage(`archie.seen.${kb.id}`));
  return (
    <li>
      <Link
        to={`/kb/${kb.id}/ask`}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex h-8 items-center gap-2.5 rounded-md px-2 text-sm",
          active ? "bg-rail-active font-medium" : "hover:bg-rail-active/60",
        )}
      >
        <Tile name={kb.name} />
        <span className="min-w-0 flex-1 truncate">{kb.name}</span>
        {building ? (
          <Loader2 className="size-3.5 animate-spin text-rail-muted" aria-label="Building" />
        ) : unseen ? (
          <span className="size-2 rounded-full bg-accent" role="img" aria-label="Build finished" />
        ) : kb.status !== "ready" ? (
          <StatusDot status={kb.status} />
        ) : null}
      </Link>
    </li>
  );
}

function ServiceStatus() {
  const ready = useQuery({ queryKey: ["ready"], queryFn: api.ready, refetchInterval: 20_000, retry: false });
  const healthy = ready.isSuccess && ready.data.qdrant && ready.data.ollama;
  return (
    <span
      role="img"
      aria-label={ready.isLoading ? "Checking services" : healthy ? "All services running" : "A service is unreachable"}
      className={cn("size-2 rounded-full", ready.isLoading ? "bg-faint" : healthy ? "bg-rail-ok" : "bg-rail-bad")}
    />
  );
}

function PreferencesDialog({
  open,
  onClose,
  theme,
  setTheme,
}: {
  open: boolean;
  onClose: () => void;
  theme: Theme;
  setTheme: (theme: Theme) => void;
}) {
  const [key, setKey] = useState(getApiKey);
  const changed = key.trim() !== getApiKey();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Preferences"
      footer={
        <Button
          variant="primary"
          onClick={() => {
            if (changed) {
              setApiKey(key.trim());
              window.location.reload();
            }
            onClose();
          }}
        >
          Done
        </Button>
      }
    >
      <div className="space-y-5">
        <Group title="Appearance">
          <Row label="Theme">
            <Segmented
              label="Theme"
              value={theme}
              onChange={setTheme}
              options={[
                { value: "system", label: "Auto" },
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark" },
              ]}
            />
          </Row>
        </Group>
        <Group
          title="Access key"
          footer="Only needed when whoever runs Archie has protected it with a key. It is stored in this browser."
        >
          <div className="px-4 py-3">
            <label htmlFor="api-key" className="sr-only">
              Access key
            </label>
            <Input id="api-key" type="password" value={key} onChange={(e) => setKey(e.target.value)} />
          </div>
        </Group>
      </div>
    </Dialog>
  );
}

function SidebarItem({
  to,
  icon,
  children,
  trailing,
}: {
  to: string;
  icon: ReactNode;
  children: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex h-8 items-center gap-2.5 rounded-md px-2 text-sm",
          isActive ? "bg-rail-active font-medium" : "hover:bg-rail-active/60",
        )
      }
    >
      <span className="grid size-6 place-items-center text-rail-muted [&>svg]:size-4">{icon}</span>
      <span className="flex-1">{children}</span>
      {trailing}
    </NavLink>
  );
}

export const MANAGE_TABS = [
  { path: "documents", label: "Documents", icon: <FileText /> },
  { path: "settings", label: "Settings", icon: <SlidersHorizontal /> },
  { path: "builds", label: "Builds", icon: <Hammer /> },
  { path: "quality", label: "Quality", icon: <Gauge /> },
  { path: "activity", label: "Activity", icon: <Activity /> },
] as const;

/** Manage's tabs, as a Mac preferences window lays them out: icon over label, centred. */
export function ManageLayout() {
  const { kbId } = useParams();
  return (
    <div className="min-h-full bg-wall">
      <nav aria-label="Manage" className="flex justify-center gap-1 overflow-x-auto px-4 pt-3">
        {MANAGE_TABS.map((tab) => (
          <NavLink
            key={tab.path}
            to={`/kb/${kbId}/manage/${tab.path}`}
            className={({ isActive }) =>
              cn(
                "press flex min-w-20 flex-col items-center gap-1 rounded-lg px-3 py-2 text-xs font-medium",
                isActive ? "bg-panel text-accent shadow-[0_0_0_0.5px_var(--color-rule)]" : "text-slate hover:text-ink",
              )
            }
          >
            <span className="[&>svg]:size-5 [&>svg]:stroke-[1.75]">{tab.icon}</span>
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}

export function Layout() {
  const { kbId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [theme, setTheme] = useTheme();
  const [prefsOpen, setPrefsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 768px)").matches);
  const kbs = useQuery({
    queryKey: ["kbs"],
    queryFn: api.listKbs,
    refetchInterval: (query) => (query.state.data?.some((kb) => kb.status === "building") ? 2_000 : 15_000),
  });
  const kb = kbs.data?.find((item) => item.id === kbId);
  const inManage = location.pathname.includes("/manage");

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
  useEffect(() => {
    if (!kb) return;
    writeStorage("archie.last", kb.id);
    if (kb.last_job) writeStorage(`archie.seen.${kb.id}`, kb.last_job.id);
  }, [kb]);

  const railHidden = !desktop && !menuOpen;
  const title = kb?.name ?? (location.pathname.startsWith("/status") ? "Status" : "Archie");

  return (
    <div className="flex h-full bg-panel">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-panel focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      {menuOpen && !desktop && (
        <button
          type="button"
          aria-label="Close sidebar"
          className="fixed inset-0 z-30 bg-black/25"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        inert={railHidden || undefined}
        aria-hidden={railHidden || undefined}
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[260px] shrink-0 flex-col border-r border-rule bg-rail text-rail-ink backdrop-blur-2xl backdrop-saturate-150",
          "transition-transform duration-300 ease-[var(--ease-sheet)] md:static md:translate-x-0",
          railHidden ? "-translate-x-full" : "translate-x-0",
        )}
      >
        <div className="flex h-[52px] items-center px-4">
          <Link to="/" aria-label="Archie home">
            <Wordmark />
          </Link>
        </div>
        <nav aria-label="Chatbots" className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-3">
          <h2 className="px-2 pt-2 pb-1 text-xs font-semibold text-rail-muted">Chatbots</h2>
          <ul className="space-y-0.5">
            {kbs.data?.map((item) => (
              <ChatbotRow key={item.id} kb={item} active={item.id === kbId} />
            ))}
          </ul>
          {kbs.data?.length === 0 && <p className="px-2 py-1 text-sm text-rail-muted">No chatbots yet.</p>}
        </nav>
        <div className="space-y-0.5 border-t border-rule px-2.5 py-2.5">
          <Link
            to="/new"
            className="press flex h-8 items-center gap-2.5 rounded-md px-2 text-sm font-medium text-accent hover:bg-rail-active/60"
          >
            <span className="grid size-6 place-items-center">
              <Plus className="size-4" strokeWidth={2.25} />
            </span>
            New Chatbot
          </Link>
          <SidebarItem to="/status" icon={<HeartPulse />} trailing={<ServiceStatus />}>
            Status
          </SidebarItem>
          <button
            type="button"
            onClick={() => setPrefsOpen(true)}
            className="flex h-8 w-full cursor-default items-center gap-2.5 rounded-md px-2 text-sm hover:bg-rail-active/60"
          >
            <span className="grid size-6 place-items-center text-rail-muted">
              <Settings className="size-4" />
            </span>
            Preferences
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 grid h-[52px] shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-3 border-b border-rule bg-panel/85 px-3 backdrop-blur-xl md:px-5">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              aria-label="Show sidebar"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
              className="press grid size-8 cursor-default place-items-center rounded-md text-slate hover:bg-plinth md:hidden"
            >
              <Menu className="size-5" />
            </button>
            <span className={cn("truncate text-base font-semibold", kbId && "max-md:sr-only")}>{title}</span>
          </div>
          {kbId ? (
            <Segmented
              label="View"
              value={inManage ? "manage" : "ask"}
              onChange={(value) => navigate(value === "ask" ? `/kb/${kbId}/ask` : `/kb/${kbId}/manage`)}
              options={[
                { value: "ask", label: "Ask" },
                { value: "manage", label: "Manage" },
              ]}
            />
          ) : (
            <span />
          )}
          <span />
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      {prefsOpen && <PreferencesDialog open onClose={() => setPrefsOpen(false)} theme={theme} setTheme={setTheme} />}
    </div>
  );
}
