import type { ReactNode } from "react";

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
          {title && <h3 className="text-xs uppercase tracking-wider text-muted">{title}</h3>}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="card border-red/40 p-4 text-red text-sm" role="alert">
      <span className="text-muted mr-2">error:</span>
      {message}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="card border-dashed p-6 text-center text-muted text-sm">{message}</div>
  );
}

export function LoadingState({ message = "loading…" }: { message?: string }) {
  return <div className="p-6 text-center text-muted text-sm animate-pulse">{message}</div>;
}

export function StatusDot({ status }: { status: string }) {
  const color =
    status === "running"
      ? "bg-cyan"
      : status === "stopped"
        ? "bg-muted"
        : "bg-red";
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${color}`}
      title={status}
      aria-label={status}
    />
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
    <span className="text-[10px] uppercase border border-border-token rounded px-1 py-px text-muted">
      lxc
    </span>
  );
}
