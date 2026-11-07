/** Accessible UI primitives built on native elements, in the Mac vocabulary. Tokens come from styles.css. */
import { Loader2, X } from "lucide-react";
import { type ButtonHTMLAttributes, type ComponentProps, type ReactNode, useEffect, useRef } from "react";

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-ink shadow-[0_1px_1px_rgb(0_0_0/0.12)] hover:bg-accent-hover",
  secondary:
    "bg-panel text-ink shadow-[0_0_0_0.5px_var(--color-rule-strong),0_1px_1px_rgb(0_0_0/0.06)] hover:bg-plinth",
  ghost: "bg-transparent text-accent hover:bg-accent-soft",
  danger: "bg-panel text-bad shadow-[0_0_0_0.5px_var(--color-rule-strong),0_1px_1px_rgb(0_0_0/0.06)] hover:bg-bad-soft",
};

/** Button styling for links and labels that act as buttons (never nest a <button> in a link). */
export function buttonClass(variant: ButtonVariant = "secondary", size: "sm" | "md" | "lg" = "md"): string {
  return cn(
    "press inline-flex shrink-0 cursor-default items-center justify-center gap-1.5 rounded-[var(--radius-control)]",
    "font-medium whitespace-nowrap select-none disabled:pointer-events-none disabled:opacity-40",
    size === "sm" && "h-7 px-2.5 text-sm",
    size === "md" && "h-8 px-3.5 text-sm",
    size === "lg" && "h-11 rounded-[10px] px-5 text-base",
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
      {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
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

const DOT: Record<Tone, string> = {
  neutral: "bg-faint",
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-5.5 items-center gap-1 rounded-full px-2.5 text-xs font-medium whitespace-nowrap",
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

export function statusTone(status: string | null | undefined): Tone {
  return (status && STATUS[status]?.[0]) || "neutral";
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  return <Badge tone={statusTone(status)}>{statusText(status)}</Badge>;
}

/** A small status dot, as macOS shows availability: colour plus an accessible name. */
export function StatusDot({ status, className }: { status: string | null | undefined; className?: string }) {
  return (
    <span
      role="img"
      aria-label={statusText(status)}
      className={cn("inline-block size-2 shrink-0 rounded-full", DOT[statusTone(status)], className)}
    />
  );
}

/** A source: mark (S1), file name and where in it. Used for answer sources and passages. */
export function SourceCard({
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
    <Tag
      className={cn(
        "rounded-[var(--radius-group)] bg-panel px-3.5 py-3 shadow-[0_0_0_0.5px_var(--color-rule-strong)]",
        className,
      )}
    >
      {mark && <div className="mb-1 text-xs font-semibold text-accent">{mark}</div>}
      <div className="text-sm leading-snug font-semibold text-ink">{title}</div>
      {children && <div className="mt-0.5 text-xs leading-relaxed text-slate">{children}</div>}
    </Tag>
  );
}

/** Large title, as at the top of a Mac app's content pane. */
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
    <div className="flex flex-wrap items-end justify-between gap-4 px-5 pt-7 pb-5 md:px-8">
      <div className="min-w-0 max-w-2xl">
        <h1 className="font-display text-2xl font-bold tracking-[-0.015em] text-balance">{title}</h1>
        {description && <p className="mt-1.5 text-base text-slate">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** An inset grouped section (System Settings style): optional header above, rows separated by inset hairlines. */
export function Group({
  title,
  footer,
  children,
  className,
}: {
  title?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={className}>
      {title && <h2 className="mb-1.5 px-3 text-sm font-semibold text-slate">{title}</h2>}
      <div className="divide-y divide-rule overflow-hidden rounded-[var(--radius-group)] bg-panel shadow-[0_0_0_0.5px_var(--color-rule)]">
        {children}
      </div>
      {footer && <p className="mt-1.5 px-3 text-xs text-slate">{footer}</p>}
    </section>
  );
}

/** One row of a grouped section: label (and detail) leading, control or value trailing. */
export function Row({
  label,
  detail,
  children,
  className,
}: {
  label: ReactNode;
  detail?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-11 items-center gap-4 px-4 py-2.5", className)}>
      <div className="min-w-0 flex-1">
        <div className="text-base">{label}</div>
        {detail && <div className="text-sm text-slate">{detail}</div>}
      </div>
      {children !== undefined && (
        <div className="flex shrink-0 items-center gap-2 text-base text-slate">{children}</div>
      )}
    </div>
  );
}

/** Segmented control: mutually exclusive choices (tabs in a toolbar, filters). */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  className,
}: {
  value: T;
  options: readonly { value: T; label: ReactNode }[];
  onChange: (value: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex rounded-[7px] bg-plinth p-0.5", className)}>
      {options.map((option) => {
        const on = option.value === value;
        return (
          // biome-ignore lint/a11y/useSemanticElements: a segmented control is a radio group of buttons
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(option.value)}
            className={cn(
              "press h-6 cursor-default rounded-[5px] px-3 text-sm font-medium whitespace-nowrap",
              on
                ? "bg-panel text-ink shadow-[0_1px_2px_rgb(0_0_0/0.12),0_0_0_0.5px_rgb(0_0_0/0.06)]"
                : "text-slate hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

const CONTROL =
  "rounded-[var(--radius-control)] bg-panel px-2.5 text-base text-ink shadow-[0_0_0_0.5px_var(--color-rule-strong),inset_0_0.5px_1px_rgb(0_0_0/0.06)] " +
  "placeholder:text-faint focus:outline-none focus:shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_35%,transparent)] " +
  "disabled:opacity-50 aria-[invalid=true]:shadow-[0_0_0_1.5px_var(--color-bad)]";

/** Controls fill their container unless the caller sets a width. */
export function width(className: string | undefined): string {
  return className && /(^|\s)([\w-]+:)?w-/.test(className) ? "" : "w-full";
}

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input {...props} className={cn(CONTROL, width(className), "h-8", className)} />;
}

export function Select({ className, children, ...props }: ComponentProps<"select">) {
  return (
    <select {...props} className={cn(CONTROL, width(className), "h-8 pr-7", className)}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea {...props} className={cn(CONTROL, width(className), "py-2 leading-relaxed", className)} />;
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
        "relative inline-flex h-[22px] w-[38px] shrink-0 cursor-default items-center rounded-full transition-colors duration-200",
        checked ? "bg-accent" : "bg-rule-strong",
        "disabled:opacity-50",
      )}
    >
      <span
        className={cn(
          "inline-block size-[18px] rounded-full bg-white shadow-[0_1px_3px_rgb(0_0_0/0.25),0_0_0_0.5px_rgb(0_0_0/0.04)]",
          "transition-transform duration-200 ease-[var(--ease-out)]",
          checked ? "translate-x-[18px]" : "translate-x-[2px]",
        )}
      />
    </button>
  );
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-16 text-center">
      {icon && <div className="mb-1 text-faint [&>svg]:size-10 [&>svg]:stroke-[1.5]">{icon}</div>}
      <h2 className="text-lg font-semibold">{title}</h2>
      {children && <div className="max-w-md text-base text-slate">{children}</div>}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-3 py-6" role="status" aria-label="Loading">
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={`line-${index.toString()}`}
          className="h-3.5 animate-pulse rounded-full bg-plinth"
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
    <div role="alert" className="rounded-[var(--radius-group)] bg-bad-soft px-4 py-3 text-sm text-bad">
      {message}
    </div>
  );
}

/** Native modal <dialog> as a macOS sheet (center) or a trailing panel (side). Focus trap, Escape and focus return come from the browser. */
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
        "bg-panel p-0 text-ink shadow-[0_0_0_0.5px_rgb(0_0_0/0.12),0_22px_70px_rgb(0_0_0/0.28)]",
        variant === "center"
          ? "m-auto w-[calc(100%-2rem)] max-w-lg rounded-[var(--radius-sheet)]"
          : "side-panel my-2 mr-2 ml-auto h-[calc(100dvh-1rem)] max-h-[calc(100dvh-1rem)] w-[calc(100%-1rem)] max-w-2xl rounded-[var(--radius-sheet)]",
      )}
    >
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="press grid size-7 cursor-default place-items-center rounded-full bg-plinth text-slate hover:text-ink"
          >
            <X className="size-3.5" strokeWidth={2.5} />
          </button>
        </div>
        <div className={cn("overflow-y-auto", variant === "center" ? "max-h-[70vh] px-5 pt-2 pb-5" : "flex-1")}>
          {children}
        </div>
        {footer && <div className="flex justify-end gap-2 px-5 pt-2 pb-5">{footer}</div>}
      </div>
    </dialog>
  );
}
