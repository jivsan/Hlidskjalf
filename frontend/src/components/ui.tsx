import type { ReactNode } from "react";

/**
 * Page header: the frame every view hangs under. The eyebrow names the section
 * (a labeled hairline), the title carries it in the display face, and actions
 * sit to the right. Keeps every page opening on the same rhythm.
 */
export function PageHeader({
  eyebrow,
  title,
  sub,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
        <h1 className="font-display text-2xl leading-none tracking-tight text-fg">{title}</h1>
        {sub && <p className="text-muted text-sm mt-1.5">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  children,
  className = "",
  actions,
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
  actions?: ReactNode;
}) {
  return (
    <div className={`card p-4 ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between mb-3">
          {title && (
            <h3 className="text-[11px] font-medium uppercase tracking-eyebrow text-muted">{title}</h3>
          )}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="card border-red/40 p-4 text-red text-sm flex items-start gap-2" role="alert">
      <span className="text-muted uppercase text-[11px] tracking-eyebrow mt-0.5 shrink-0">error</span>
      <span>{message}</span>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="well border-dashed p-6 text-center text-muted text-sm">{message}</div>
  );
}

export function LoadingState({ message = "loading…" }: { message?: string }) {
  return (
    <div className="p-6 flex items-center justify-center gap-2 text-muted text-sm" role="status">
      <span className="spinner" aria-hidden="true" />
      {message}
    </div>
  );
}

export function StatusDot({ status }: { status: string }) {
  const color =
    status === "running"
      ? "bg-cyan"
      : status === "stopped"
        ? "bg-muted"
        : "bg-red";
  const live = status === "running";
  return (
    <span className="relative inline-flex" title={status} aria-label={status}>
      <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
      {live && (
        <span className="absolute inset-0 rounded-full bg-cyan/60 animate-ping" aria-hidden="true" />
      )}
    </span>
  );
}

/**
 * Meter/progress bar. Fill carries severity; the unfilled track is a darker
 * step of the same family (via low-opacity fill color).
 */
export function ProgressBar({
  fraction,
  color,
  className = "",
}: {
  fraction: number; // 0..1, clamped for display
  color?: "cyan" | "pink" | "amber" | "red";
  className?: string;
}) {
  const pct = Math.max(0, Math.min(1, fraction)) * 100;
  const auto: "cyan" | "amber" | "pink" =
    fraction >= 1 ? "pink" : fraction >= 0.8 ? "amber" : "cyan";
  const c = color ?? auto;
  const fill =
    c === "cyan" ? "bg-cyan" : c === "amber" ? "bg-amber" : c === "red" ? "bg-red" : "bg-pink";
  const track =
    c === "cyan"
      ? "bg-cyan/10"
      : c === "amber"
        ? "bg-amber/10"
        : c === "red"
          ? "bg-red/10"
          : "bg-pink/10";
  return (
    <div className={`h-2 rounded-full overflow-hidden ${track} ${className}`}>
      <div
        className={`h-full rounded-full ${fill}`}
        style={{ width: `${pct}%`, transition: "width 150ms ease" }}
      />
    </div>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  if (kind !== "lxc") return null;
  return (
    <span className="text-[10px] uppercase tracking-wider border border-border-token rounded px-1 py-px text-muted align-middle">
      lxc
    </span>
  );
}
