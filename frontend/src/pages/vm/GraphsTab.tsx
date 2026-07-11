import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../../api";
import {
  AXIS_TICK,
  CHART,
  ChartLegend,
  ChartTooltip,
  Gauge,
  MetricAreaChart,
  TimeframePills,
  type SeriesDef,
} from "../../components/charts";
import { Card, EmptyState, ErrorState, LoadingState, ProgressBar } from "../../components/ui";
import { usePoll } from "../../hooks/usePoll";
import { formatBytes, formatPercent, formatRate, MONTH_NAMES } from "../../lib/format";
import type {
  BandwidthMonthly,
  BandwidthRange,
  Timeframe,
  VmDetail,
  VmMetricPoint,
} from "../../types";

export function GraphsTab({ vm }: { vm: VmDetail }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const sub = searchParams.get("sub") === "bandwidth" ? "bandwidth" : "system";
  const setSub = (s: "system" | "bandwidth") => {
    const next: Record<string, string> = { tab: "graphs" };
    if (s === "bandwidth") next.sub = "bandwidth";
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {(["system", "bandwidth"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setSub(s)}
            className={`px-3 py-1.5 rounded-card text-sm border ${
              sub === s
                ? "border-pink/70 text-pink bg-pink/10"
                : "border-border-token text-muted hover:text-fg"
            }`}
          >
            {s === "system" ? "System Statistics" : "Bandwidth Statistics"}
          </button>
        ))}
      </div>
      {sub === "system" ? <SystemStats vm={vm} /> : <BandwidthStats vmid={vm.vmid} />}
    </div>
  );
}

// ---------------- System Statistics ----------------

function SystemStats({ vm }: { vm: VmDetail }) {
  const [timeframe, setTimeframe] = useState<Timeframe>("hour");
  const metrics = usePoll(
    () => api.get<VmMetricPoint[]>(`/api/vms/${vm.vmid}/metrics?timeframe=${timeframe}&cf=AVERAGE`),
    60000,
  );

  const points = metrics.data ?? [];

  const cpuFraction = vm.maxcpu > 0 ? vm.cpu / vm.maxcpu : null;
  const ramFraction = vm.maxmem > 0 ? vm.mem / vm.maxmem : null;
  const diskFraction = vm.maxdisk > 0 ? vm.disk / vm.maxdisk : null;

  // CPU chart: convert core-sum fraction into % of the VM's total cores.
  const cpuData = useMemo(
    () =>
      points.map((p) => ({
        t: p.t,
        cpuPct: p.cpu != null && (p.maxcpu ?? vm.maxcpu) > 0 ? p.cpu / (p.maxcpu ?? vm.maxcpu)! : null,
      })),
    [points, vm.maxcpu],
  );

  if (metrics.loading) return <LoadingState />;
  if (metrics.error && points.length === 0) return <ErrorState message={metrics.error} />;

  const pct = (v: number) => formatPercent(v, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <TimeframePills value={timeframe} onChange={setTimeframe} />
        {metrics.error && <span className="text-xs text-red">{metrics.error}</span>}
      </div>

      <Card title="Current utilization">
        <div className="flex flex-wrap justify-around gap-4">
          <Gauge fraction={cpuFraction} label="cpu" detail={`${vm.maxcpu} cores`} />
          <Gauge
            fraction={ramFraction}
            label="ram"
            detail={`${formatBytes(vm.mem)} / ${formatBytes(vm.maxmem)}`}
          />
          <Gauge
            fraction={diskFraction}
            label="disk"
            detail={`${formatBytes(vm.disk)} / ${formatBytes(vm.maxdisk)}`}
          />
        </div>
      </Card>

      <Card title="CPU %">
        <MetricAreaChart
          data={cpuData}
          timeframe={timeframe}
          yFormat={pct}
          yDomain={[0, 1]}
          series={[{ key: "cpuPct", name: "cpu", color: CHART.cyan, format: (v) => formatPercent(v) }]}
        />
      </Card>

      <Card title="RAM">
        <MetricAreaChart
          data={points}
          timeframe={timeframe}
          yFormat={formatBytes}
          series={[
            { key: "mem", name: "used", color: CHART.cyan, format: formatBytes },
            { key: "maxmem", name: "max", color: CHART.pink, format: formatBytes },
          ]}
        />
      </Card>

      <Card title="Disk usage">
        <MetricAreaChart
          data={points}
          timeframe={timeframe}
          yFormat={formatBytes}
          series={[
            { key: "disk", name: "used", color: CHART.cyan, format: formatBytes },
            { key: "maxdisk", name: "max", color: CHART.pink, format: formatBytes },
          ]}
        />
      </Card>

      <Card title="Disk I/O">
        <MetricAreaChart
          data={points}
          timeframe={timeframe}
          yFormat={formatRate}
          series={[
            { key: "diskread", name: "read", color: CHART.cyan, format: formatRate },
            { key: "diskwrite", name: "write", color: CHART.pink, format: formatRate },
          ]}
        />
      </Card>

      <Card title="Network">
        <MetricAreaChart
          data={points}
          timeframe={timeframe}
          yFormat={formatRate}
          series={[
            { key: "netin", name: "in", color: CHART.cyan, format: formatRate },
            { key: "netout", name: "out", color: CHART.pink, format: formatRate },
          ]}
        />
      </Card>
    </div>
  );
}

// ---------------- Bandwidth Statistics ----------------

function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const BW_SERIES: SeriesDef[] = [
  { key: "bytes_in", name: "in", color: CHART.cyan, format: formatBytes },
  { key: "bytes_out", name: "out", color: CHART.pink, format: formatBytes },
];

function BandwidthStats({ vmid }: { vmid: number }) {
  const now = new Date();
  const [from, setFrom] = useState(isoDate(new Date(now.getFullYear(), now.getMonth(), 1)));
  const [to, setTo] = useState(isoDate(now));
  const [year, setYear] = useState(now.getFullYear());

  const range = usePoll(
    () => api.get<BandwidthRange>(`/api/vms/${vmid}/bandwidth?from=${from}&to=${to}`),
    120000,
  );
  const monthly = usePoll(
    () => api.get<BandwidthMonthly>(`/api/vms/${vmid}/bandwidth/monthly?year=${year}`),
    120000,
  );

  const dayData = useMemo(
    () =>
      (range.data?.days ?? []).map((d) => ({
        t: Math.floor(new Date(`${d.date}T00:00:00`).getTime() / 1000),
        bytes_in: d.bytes_in,
        bytes_out: d.bytes_out,
      })),
    [range.data],
  );

  const monthData = useMemo(
    () =>
      (monthly.data?.months ?? []).map((m) => ({
        name: MONTH_NAMES[m.month - 1],
        bytes_in: m.bytes_in,
        bytes_out: m.bytes_out,
      })),
    [monthly.data],
  );

  const totals = range.data?.totals;
  const quota = range.data?.quota_gb ?? null;
  const utilization = range.data?.utilization ?? null;

  return (
    <div className="space-y-4">
      {/* filter row above everything it scopes */}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label" htmlFor="bw-from">from</label>
          <input
            id="bw-from"
            type="date"
            className="input w-auto"
            value={from}
            max={to}
            onChange={(e) => setFrom(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="bw-to">to</label>
          <input
            id="bw-to"
            type="date"
            className="input w-auto"
            value={to}
            min={from}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
        {range.error && <span className="text-xs text-red mb-2">{range.error}</span>}
      </div>

      <Card title={quota != null ? "Quota" : "Utilized"}>
        {range.loading ? (
          <LoadingState />
        ) : totals ? (
          <div className="space-y-2 metric text-sm">
            {quota != null ? (
              <>
                <div className="flex flex-wrap gap-x-8 gap-y-1">
                  <span>
                    <span className="text-muted">limit</span> {quota} GB
                  </span>
                  <span>
                    <span className="text-muted">utilized</span> {formatBytes(totals.total)}
                  </span>
                  <span>
                    <span className="text-muted">utilization</span>{" "}
                    <span
                      className={
                        (utilization ?? 0) >= 1
                          ? "text-pink"
                          : (utilization ?? 0) >= 0.8
                            ? "text-amber"
                            : "text-cyan"
                      }
                    >
                      {formatPercent(utilization)}
                    </span>
                  </span>
                </div>
                <ProgressBar fraction={utilization ?? 0} />
              </>
            ) : (
              <div className="flex flex-wrap gap-x-8 gap-y-1">
                <span>
                  <span style={{ color: CHART.cyan }}>▮</span> in {formatBytes(totals.bytes_in)}
                </span>
                <span>
                  <span style={{ color: CHART.pink }}>▮</span> out {formatBytes(totals.bytes_out)}
                </span>
                <span>
                  <span className="text-muted">total</span> {formatBytes(totals.total)}
                </span>
              </div>
            )}
          </div>
        ) : (
          <EmptyState message="no bandwidth data for this range yet" />
        )}
      </Card>

      <Card title="Daily traffic">
        {range.loading ? (
          <LoadingState />
        ) : dayData.length === 0 ? (
          <EmptyState message="no daily data in this range — the accumulator may not have run yet" />
        ) : (
          <MetricAreaChart
            data={dayData}
            timeframe="month"
            yFormat={formatBytes}
            stacked
            series={BW_SERIES}
          />
        )}
      </Card>

      <Card
        title="Monthly"
        actions={
          <div className="flex items-center gap-2 text-sm metric">
            <button className="btn-plain px-2 py-0.5" onClick={() => setYear((y) => y - 1)} aria-label="previous year">
              ‹
            </button>
            <span>{year}</span>
            <button
              className="btn-plain px-2 py-0.5"
              onClick={() => setYear((y) => y + 1)}
              disabled={year >= now.getFullYear()}
              aria-label="next year"
            >
              ›
            </button>
          </div>
        }
      >
        {monthly.loading ? (
          <LoadingState />
        ) : monthly.error && monthData.length === 0 ? (
          <ErrorState message={monthly.error} />
        ) : monthData.every((m) => m.bytes_in === 0 && m.bytes_out === 0) ? (
          <EmptyState message={`no traffic recorded in ${year}`} />
        ) : (
          <div className="metric">
            <ChartLegend series={BW_SERIES} />
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={monthData} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={CHART.grid} strokeWidth={1} vertical={false} />
                <XAxis
                  dataKey="name"
                  tick={AXIS_TICK}
                  axisLine={{ stroke: CHART.grid }}
                  tickLine={false}
                />
                <YAxis
                  tickFormatter={formatBytes}
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  width={64}
                />
                <Tooltip
                  content={<ChartTooltip series={BW_SERIES} />}
                  cursor={{ fill: "rgba(86, 95, 137, 0.12)" }}
                  isAnimationActive={false}
                />
                <Bar
                  dataKey="bytes_in"
                  name="in"
                  stackId="bw"
                  fill={CHART.cyan}
                  maxBarSize={24}
                  stroke={CHART.surface}
                  strokeWidth={1}
                  isAnimationActive={false}
                />
                <Bar
                  dataKey="bytes_out"
                  name="out"
                  stackId="bw"
                  fill={CHART.pink}
                  maxBarSize={24}
                  radius={[4, 4, 0, 0]}
                  stroke={CHART.surface}
                  strokeWidth={1}
                  isAnimationActive={false}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>
    </div>
  );
}
