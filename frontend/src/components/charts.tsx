import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";
import type { Timeframe } from "../types";
import { EmptyState } from "./ui";
import { cssVar } from "../lib/theme";

// Chart colors read the design tokens (the `--c-*` CSS custom properties
// emitted from tailwind.config.js) lazily, per access — a theme change is a
// repaint, not a rebuild. Fallbacks mirror the tailwind hexes so a no-DOM or
// pre-CSS context still renders the same palette. Keep the exported API
// (`CHART.cyan` etc.) identical for consumers (OverviewTab, NodePage,
// GraphsTab, this file).
const CHART_FALLBACKS = {
  cyan: "#2de2e6",
  pink: "#ff4fa3",
  amber: "#e0af68",
  red: "#f7768e",
  grid: "#2b2f45", // --c-border-token
  muted: "#727aa3",
  fg: "#c8d3f5",
  surface: "#1e2030",
  bg: "#15161f",
} as const;

const CHART_VARS: Record<keyof typeof CHART_FALLBACKS, string> = {
  cyan: "--c-cyan",
  pink: "--c-pink",
  amber: "--c-amber",
  red: "--c-red",
  grid: "--c-border-token",
  muted: "--c-muted",
  fg: "--c-fg",
  surface: "--c-surface",
  bg: "--c-bg",
};

export const CHART: { readonly [K in keyof typeof CHART_FALLBACKS]: string } =
  new Proxy(CHART_FALLBACKS, {
    get(target, prop: string | symbol) {
      const key = prop as keyof typeof CHART_FALLBACKS;
      if (typeof prop === "string" && key in CHART_VARS) {
        return cssVar(CHART_VARS[key], target[key]);
      }
      return Reflect.get(target, prop);
    },
  });

export const AXIS_TICK = { fill: CHART.muted, fontSize: 11, fontFamily: "inherit" } as const;

// --- time axis formatting per timeframe ---

export function timeTickFormatter(timeframe: Timeframe): (t: number) => string {
  return (t: number) => {
    const d = new Date(t * 1000);
    const pad = (n: number) => String(n).padStart(2, "0");
    switch (timeframe) {
      case "hour":
      case "day":
        return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      case "week":
      case "month":
        return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }
  };
}

export function timeLabelFormatter(t: number): string {
  const d = new Date(t * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// --- shared tooltip: values lead (strong), series names secondary, line keys ---

export interface SeriesDef {
  key: string;
  name: string;
  color: string;
  format: (v: number) => string;
}

export function ChartTooltip({
  active,
  payload,
  label,
  series,
  labelFormat,
}: TooltipProps<number, string> & {
  series: SeriesDef[];
  labelFormat?: (label: number) => string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const byKey = new Map(payload.map((p) => [p.dataKey as string, p.value]));
  const labelText =
    labelFormat && typeof label === "number" ? labelFormat(label) : String(label ?? "");
  return (
    <div className="card px-3 py-2 text-xs shadow-lg bg-surface">
      <div className="text-muted mb-1 metric">{labelText}</div>
      {series.map((s) => {
        const v = byKey.get(s.key);
        return (
          <div key={s.key} className="flex items-center gap-2 py-px">
            <span className="inline-block w-3 h-0.5" style={{ background: s.color }} />
            <span className="text-fg metric font-medium">
              {v == null || typeof v !== "number" ? "—" : s.format(v)}
            </span>
            <span className="text-muted">{s.name}</span>
          </div>
        );
      })}
    </div>
  );
}

export function ChartLegend({ series }: { series: SeriesDef[] }) {
  if (series.length < 2) return null;
  return (
    <div className="flex gap-4 text-xs text-muted mb-1">
      {series.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5" style={{ background: s.color }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

// --- timeframe pills ---

const TIMEFRAMES: Timeframe[] = ["hour", "day", "week", "month"];

export function TimeframePills({
  value,
  onChange,
}: {
  value: Timeframe;
  onChange: (t: Timeframe) => void;
}) {
  return (
    <div className="flex gap-1" role="tablist" aria-label="timeframe">
      {TIMEFRAMES.map((tf) => (
        <button
          key={tf}
          role="tab"
          aria-selected={value === tf}
          onClick={() => onChange(tf)}
          className={`px-3 py-1 rounded-card text-xs border ${
            value === tf
              ? "border-pink/70 text-pink bg-pink/10"
              : "border-border-token text-muted hover:text-fg"
          }`}
        >
          {tf.charAt(0).toUpperCase() + tf.slice(1)}
        </button>
      ))}
    </div>
  );
}

// --- generic time-series area chart (1–2 series) ---

let gradientSeq = 0;

export function MetricAreaChart<T extends { t: number }>({
  data,
  series,
  timeframe,
  yFormat,
  yDomain,
  height = 200,
  stacked = false,
}: {
  data: T[];
  series: SeriesDef[];
  timeframe: Timeframe;
  yFormat: (v: number) => string;
  yDomain?: [number | "auto" | "dataMin", number | "auto" | "dataMax"];
  height?: number;
  stacked?: boolean;
}) {
  // chart-draw: the stroke draws itself in on mount (≤600ms), then animation
  // is disabled for the lifetime of the component — the Node page polls every
  // few seconds and must not pay for a re-animation on every sample. Draw-in
  // is a JS animation, so reduced-motion is honored here, not in CSS.
  const firstRender = useRef(
    !(
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ),
  );
  useEffect(() => {
    const t = window.setTimeout(() => {
      firstRender.current = false;
    }, 700);
    return () => window.clearTimeout(t);
  }, []);

  const hasData = data.some((d) => series.some((s) => (d as Record<string, unknown>)[s.key] != null));
  if (!hasData) {
    return <EmptyState message="no data for this range" />;
  }
  const gid = `grad${gradientSeq++}`;
  return (
    <div className="metric">
      <ChartLegend series={series} />
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <defs>
            {series.map((s, i) => (
              <linearGradient key={s.key} id={`${gid}-${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={0.25} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0.03} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke={CHART.grid} strokeWidth={1} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={timeTickFormatter(timeframe)}
            tick={AXIS_TICK}
            axisLine={{ stroke: CHART.grid }}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            tickFormatter={yFormat}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            width={64}
            domain={yDomain ?? [0, "auto"]}
          />
          <Tooltip
            content={
              <ChartTooltip series={series} labelFormat={timeLabelFormatter} />
            }
            cursor={{ stroke: CHART.muted, strokeWidth: 1 }}
            isAnimationActive={false}
          />
          {series.map((s, i) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={i === 0 ? 1.75 : 1.5}
              fill={`url(#${gid}-${i})`}
              stackId={stacked ? "stack" : undefined}
              dot={false}
              connectNulls={false}
              isAnimationActive={firstRender.current}
              animationDuration={600}
              // The main line carries a faint neon bloom; secondary series
              // stay flat. One filter per chart, repainted only per poll.
              style={
                i === 0
                  ? { filter: `drop-shadow(0 0 3px color-mix(in srgb, ${s.color} 55%, transparent))` }
                  : undefined
              }
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- radial gauge (current utilization) ---

export function Gauge({
  fraction,
  label,
  detail,
}: {
  fraction: number | null; // 0..1
  label: string;
  detail?: ReactNode;
}) {
  const f = fraction == null ? 0 : Math.max(0, Math.min(1, fraction));
  const r = 34;
  const c = 2 * Math.PI * r;
  // sweep 270° arc
  const arc = 0.75;
  const dashFull = c * arc;
  const dash = dashFull * f;
  const color = f >= 1 ? CHART.red : f >= 0.8 ? CHART.amber : CHART.pink;
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="96" height="96" viewBox="0 0 96 96" role="img" aria-label={`${label} gauge`}>
        <g transform="rotate(135 48 48)">
          <circle
            cx="48"
            cy="48"
            r={r}
            fill="none"
            stroke={CHART.grid}
            strokeWidth="7"
            strokeDasharray={`${dashFull} ${c}`}
            strokeLinecap="round"
          />
          <circle
            cx="48"
            cy="48"
            r={r}
            fill="none"
            stroke={color}
            strokeWidth="7"
            strokeDasharray={`${dash} ${c}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dasharray 150ms ease, stroke 150ms ease" }}
          />
        </g>
        <text
          x="48"
          y="52"
          textAnchor="middle"
          fill={CHART.fg}
          fontSize="15"
          fontFamily="inherit"
        >
          {fraction == null ? "—" : `${Math.round(f * 100)}%`}
        </text>
      </svg>
      <div className="text-xs text-muted uppercase tracking-wider">{label}</div>
      {detail && <div className="text-xs text-muted metric">{detail}</div>}
    </div>
  );
}

// --- sparkline (overview cards) ---

export function Sparkline<T extends { t: number }>({
  data,
  series,
  height = 48,
}: {
  data: T[];
  series: SeriesDef[];
  height?: number;
}) {
  const hasData = data.some((d) => series.some((s) => (d as Record<string, unknown>)[s.key] != null));
  if (!hasData) {
    return <div className="text-muted text-xs py-4 text-center">no data yet</div>;
  }
  const gid = `spark${gradientSeq++}`;
  return (
    <div className="metric">
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            {series.map((s, i) => (
              <linearGradient key={s.key} id={`${gid}-${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={0.25} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0.03} />
              </linearGradient>
            ))}
          </defs>
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide />
          <YAxis hide domain={[0, "auto"]} />
          <Tooltip
            content={<ChartTooltip series={series} labelFormat={timeLabelFormatter} />}
            cursor={{ stroke: CHART.muted, strokeWidth: 1 }}
            isAnimationActive={false}
          />
          {series.map((s, i) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={1.5}
              fill={`url(#${gid}-${i})`}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
      <ChartLegend series={series} />
    </div>
  );
}
