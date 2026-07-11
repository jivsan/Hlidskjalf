// Formatting helpers — no raw byte numbers in the UI, ever.

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

/** 1024-based, auto unit, ~2 significant decimals. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !isFinite(bytes)) return "—";
  const neg = bytes < 0;
  let v = Math.abs(bytes);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  const decimals = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  const s = v.toFixed(i === 0 ? 0 : decimals);
  return `${neg ? "-" : ""}${s} ${UNITS[i]}`;
}

/** Bytes-per-second rate with auto unit. */
export function formatRate(bytesPerSec: number | null | undefined): string {
  if (bytesPerSec == null || !isFinite(bytesPerSec)) return "—";
  return `${formatBytes(bytesPerSec)}/s`;
}

/** "4d 2h" style uptime from seconds. */
export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Percent from a 0..1 fraction. */
export function formatPercent(fraction: number | null | undefined, decimals = 1): string {
  if (fraction == null || !isFinite(fraction)) return "—";
  return `${(fraction * 100).toFixed(decimals)}%`;
}

/** Unix seconds → local date-time string. */
export function formatDateTime(unixSec: number | null | undefined): string {
  if (unixSec == null) return "—";
  const d = new Date(unixSec * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Duration between two unix-second stamps, "1m 23s" style. */
export function formatDuration(startSec: number, endSec: number): string {
  const s = Math.max(0, Math.round(endSec - startSec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
