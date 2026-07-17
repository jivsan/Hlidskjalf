import type { CSSProperties, ReactElement, ReactNode } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
    // tooltip-shard: the chamfered cut + left accent strip live in index.css;
    // the strip takes the FIRST series' color, threaded in like .toast's
    // --toast-accent.
    <div
      className="tooltip-shard shard-sm px-3 py-2 text-xs"
      style={{ "--tooltip-accent": series[0]?.color } as CSSProperties}
    >
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

// live-edge pulse dot: the last point WITH a value on the primary series
// wears the fleet's LED language — a small filled circle breathing the
// dot-bloom box-shadow, "this feed is alive". box-shadow is HTML-only, so the
// dot is an HTML span inside a <foreignObject>; the wrapper is oversized so
// the bloom is never clipped by the foreignObject bounds, and pointer-events
// stay off so the tooltip still owns hover. Null tails are handled by
// scanning back for the last point that actually has a value, not by trusting
// the array's last row.
function liveEdgeDot<T extends { t: number }>(
  data: T[],
  key: string,
  color: string,
  size: number,
  bloomClass: string,
): (props: { cx?: number; cy?: number; index?: number }) => ReactElement<SVGElement> {
  let liveIndex = -1;
  for (let i = data.length - 1; i >= 0; i--) {
    if ((data[i] as Record<string, unknown>)[key] != null) {
      liveIndex = i;
      break;
    }
  }
  const box = size * 4; // the bloom must fit inside the foreignObject
  // recharts' AreaDot type claims ReactElement, but returning null is the
  // documented "render nothing" path for every point that isn't the live edge.
  return ((props: { cx?: number; cy?: number; index?: number }) => {
    if (liveIndex < 0 || props.index !== liveIndex || props.cx == null || props.cy == null) {
      return null;
    }
    return (
      <foreignObject
        x={props.cx - box / 2}
        y={props.cy - box / 2}
        width={box}
        height={box}
        style={{ pointerEvents: "none" }}
      >
        <div
          style={{
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <span
            className={bloomClass}
            style={
              {
                display: "block",
                width: size,
                height: size,
                borderRadius: 9999,
                background: color,
                "--dot-bloom-color": color,
              } as CSSProperties
            }
          />
        </div>
      </foreignObject>
    );
  }) as (props: { cx?: number; cy?: number; index?: number }) => ReactElement<SVGElement>;
}

// The hover crossline is a live instrument: cyan at half power with a 1px
// soft glow, not the muted hairline it used to be. Built per render so the
// token reads stay lazy like every other CHART.* access.
function chartCursor() {
  return {
    stroke: CHART.cyan,
    strokeOpacity: 0.5,
    strokeWidth: 1,
    style: { filter: `drop-shadow(0 0 1px ${CHART.cyan})` },
  } as const;
}

// Area fills read as light falling off a signal: brightest at the stroke,
// gone by the baseline — not a flat wash.
function AreaGradient({ id, color }: { id: string; color: string }) {
  return (
    <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stopColor={color} stopOpacity={0.32} />
      <stop offset="55%" stopColor={color} stopOpacity={0.1} />
      <stop offset="100%" stopColor={color} stopOpacity={0.02} />
    </linearGradient>
  );
}

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
        {/* top/right margins give the live-edge dot's bloom room to breathe
            past the last point, which sits on the plot's right edge. */}
        <AreaChart data={data} margin={{ top: 16, right: 16, bottom: 0, left: 0 }}>
          <defs>
            {series.map((s, i) => (
              <AreaGradient key={s.key} id={`${gid}-${i}`} color={s.color} />
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
            cursor={chartCursor()}
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
              dot={i === 0 ? liveEdgeDot(data, s.key, s.color, 8, "dot-bloom") : false}
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
  const color = f >= 1 ? CHART.red : f >= 0.8 ? CHART.amber : CHART.pink;

  // gauge-sweep: the first real reading sweeps the arc in from empty while
  // the center count climbs with it — one 600ms rAF ease-out, then the 150ms
  // CSS transition takes over for poll updates. Reduced-motion renders the
  // final reading instantly.
  const reduced =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hasValue = fraction != null;
  const [sweep, setSweep] = useState(() => (hasValue && !reduced ? 0 : 1));
  useLayoutEffect(() => {
    if (!hasValue || reduced) return;
    setSweep(0);
    let raf = 0;
    let start: number | null = null;
    const tick = (now: number) => {
      if (start == null) start = now;
      const t = Math.min(1, (now - start) / 600);
      setSweep(1 - Math.pow(1 - t, 3)); // ease-out cubic
      if (t < 1) raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [hasValue, reduced]);

  const sweeping = sweep < 1;
  const dash = dashFull * f * sweep;
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
            // The arc is a live reading — it carries the same faint neon bloom
            // as MetricAreaChart's main line, in its own threshold color. The
            // dash transition is suspended while the entrance drives the dash
            // per frame; it re-arms the moment the sweep lands.
            style={{
              transition: sweeping
                ? "stroke 150ms ease"
                : "stroke-dasharray 150ms ease, stroke 150ms ease",
              filter: `drop-shadow(0 0 3px color-mix(in srgb, ${color} 55%, transparent))`,
            }}
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
          {fraction == null ? "—" : `${Math.round(f * sweep * 100)}%`}
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
        {/* margins give the smaller live-edge dot's bloom room past the last
            point, which sits on the plot's right edge. */}
        <AreaChart data={data} margin={{ top: 8, right: 10, bottom: 8, left: 0 }}>
          <defs>
            {series.map((s, i) => (
              <AreaGradient key={s.key} id={`${gid}-${i}`} color={s.color} />
            ))}
          </defs>
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide />
          <YAxis hide domain={[0, "auto"]} />
          <Tooltip
            content={<ChartTooltip series={series} labelFormat={timeLabelFormatter} />}
            cursor={chartCursor()}
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
              dot={i === 0 ? liveEdgeDot(data, s.key, s.color, 5, "dot-bloom-sm") : false}
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
