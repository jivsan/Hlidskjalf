// Read a design-token CSS custom property (emitted by the tailwind plugin in
// tailwind.config.js as `--c-<color>`) with a fallback for no-DOM contexts
// (SSR, tests) and for an unset/empty value. Canvas-API consumers (Recharts,
// xterm, noVNC) can't use var(--…) directly — this resolves it for them.

export function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return fallback;
  }
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v === "" ? fallback : v;
}
