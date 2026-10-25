/** Accessible UI primitives built on native elements. Tokens come from styles.css (Gallery Labels). */
import { Loader2, X } from "lucide-react";
import { type ButtonHTMLAttributes, type ComponentProps, type ReactNode, useEffect, useRef } from "react";

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-ink border-transparent hover:bg-accent-hover",
  secondary: "bg-panel text-ink border-rule-strong hover:border-ink",
  ghost: "bg-transparent text-slate border-transparent hover:bg-plinth hover:text-ink",
  danger: "bg-panel text-bad border-rule-strong hover:border-bad hover:bg-bad-soft",
};

/** Button styling for links and labels that act as buttons (never nest a <button> in a link). */
export function buttonClass(variant: ButtonVariant = "secondary", size: "sm" | "md" | "lg" = "md"): string {
  return cn(
    "inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border font-semibold whitespace-nowrap",
    "transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45",
    size === "sm" && "h-8 px-3 text-xs",
    size === "md" && "h-10 px-4 text-sm",
    size === "lg" && "h-12 px-5 text-base",
    BUTTON[variant],
  );
}

export function Button({
  variant = "secondary",
  busy = false,
  size = "md",
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; busy?: boolean; size?: "sm" | "md" | "lg" }) {
  return (
    <button
      type="button"
      {...props}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cn(buttonClass(variant, size), className)}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

type Tone = "neutral" | "accent" | "ok" | "warn" | "bad";

const TONE: Record<Tone, string> = {
  neutral: "bg-plinth text-slate",
  accent: "bg-accent-soft text-accent",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-md px-2 text-xs font-semibold whitespace-nowrap",
        TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

/** Plain-language status words: what a non-technical user should read, never internal state names. */
const STATUS: Record<string, [Tone, string]> = {
  ready: ["ok", "Ready"],
  completed: ["ok", "Finished"],
  completed_with_errors: ["warn", "Finished with problems"],
  answered: ["ok", "Answered"],
  cache_hit: ["accent", "Answered from memory"],
  building: ["accent", "Building"],
  running: ["accent", "Running"],
  queued: ["accent", "Waiting to start"],
  stale: ["warn", "Needs rebuild"],
  abstained: ["warn", "Declined to answer"],
  draft: ["neutral", "Not built yet"],
  pending: ["neutral", "Waiting"],
  skipped: ["neutral", "Skipped"],
  cancelled: ["neutral", "Cancelled"],
  failed: ["bad", "Failed"],
  error: ["bad", "Error"],
  firing: ["bad", "Firing"],
  ok: ["ok", "OK"],
};

export function statusText(status: string | null | undefined): string {
  return status ? (STATUS[status]?.[1] ?? status.replace(/_/g, " ")) : "";
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const [tone, text] = STATUS[status] ?? ["neutral", status.replace(/_/g, " ")];
  return <Badge tone={tone}>{text}</Badge>;
}

/**
 * The signature: a museum tombstone label. Accession mark (e.g. S1) in mono oxblood, the title in semibold,
 * provenance lines in slate, on a small hairline card.
 */
export function GalleryLabel({
  title,
  mark,
  children,
  className,
  as: Tag = "div",
}: {
  title: ReactNode;
  mark?: ReactNode;
  children?: ReactNode;
  className?: string;
  as?: "div" | "li" | "article";
}) {
  return (
    <Tag className={cn("rounded-[3px] border border-rule bg-panel px-3.5 py-3", className)}>
      {mark && <div className="mb-1.5 font-mono text-xs font-semibold tracking-wide text-accent">{mark}</div>}
      <div className="leading-snug font-semibold text-ink">{title}</div>
      {children && <div className="mt-1 text-xs leading-relaxed text-slate">{children}</div>}
    </Tag>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 px-5 pt-8 pb-6 md:px-10 md:pt-10">
      <div className="min-w-0 max-w-2xl">
        <h1 className="text-xl font-semibold tracking-tight text-balance">{title}</h1>
        {description && <p className="mt-2 text-sm text-slate">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const CONTROL =
  "rounded-lg border border-rule-strong bg-panel px-3 text-sm text-ink placeholder:text-slate " +
  "hover:border-slate focus:border-accent focus:outline-none disabled:opacity-60 " +
  "aria-[invalid=true]:border-bad";

/** Controls fill their container unless the caller sets a width. */
export function width(className: string | undefined): string {
  return className && /(^|\s)([\w-]+:)?w-/.test(className) ? "" : "w-full";
}

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input {...props} className={cn(CONTROL, width(className), "h-10", className)} />;
}

export function Select({ className, children, ...props }: ComponentProps<"select">) {
  return (
    <select {...props} className={cn(CONTROL, width(className), "h-10 pr-8", className)}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea {...props} className={cn(CONTROL, width(className), "py-2.5 leading-relaxed", className)} />;
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-150",
        checked ? "bg-accent" : "bg-rule-strong",
        "disabled:opacity-50",
      )}
    >
      <span
        className={cn(
          "inline-block size-5 rounded-full bg-white shadow-[0_1px_2px_rgb(0_0_0/0.25)] transition-transform duration-150",
          checked ? "translate-x-[22px]" : "translate-x-[2px]",
        )}
      />
    </button>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="border-t border-rule pt-3">
      <div className="text-xs font-semibold text-slate">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate">{hint}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-3 py-10">
      {icon && <div className="text-slate">{icon}</div>}
      <h2 className="text-lg font-semibold">{title}</h2>
      {children && <div className="max-w-xl text-sm text-slate">{children}</div>}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-3 py-6" role="status" aria-label="Loading">
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={`line-${index.toString()}`}
          className="h-4 animate-pulse rounded bg-plinth"
          style={{ width: `${88 - index * 14}%` }}
        />
      ))}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-lg bg-bad-soft px-4 py-3 text-sm text-bad">
      {message}
    </div>
  );
}

/** Native modal <dialog>: focus trap, Escape and focus return come from the browser. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  variant = "center",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  variant?: "center" | "side";
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      // showModal() focuses the first button (Close); move focus to the field the dialog marks as primary.
      dialog.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: Escape closes the dialog natively; this handles backdrop clicks
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(event) => event.target === ref.current && onClose()}
      className={cn(
        "border border-rule bg-panel p-0 text-ink shadow-[0_24px_60px_-20px_rgb(0_0_0/0.45)]",
        variant === "center"
          ? "m-auto w-[calc(100%-2rem)] max-w-lg rounded-xl"
          : "my-0 mr-0 ml-auto h-dvh max-h-dvh w-full max-w-2xl rounded-none",
      )}
    >
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-rule px-5 py-4">
          <h2 className="text-base font-semibold">{title}</h2>
          <Button variant="ghost" size="sm" aria-label="Close" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </div>
        <div className={cn("overflow-y-auto", variant === "center" ? "max-h-[70vh] px-5 py-5" : "flex-1")}>
          {children}
        </div>
        {footer && <div className="flex justify-end gap-2 border-t border-rule px-5 py-4">{footer}</div>}
      </div>
    </dialog>
  );
}
