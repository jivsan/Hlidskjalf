import { useState } from "react";
import { api } from "../api";
import {
  CHART,
  MetricAreaChart,
  TimeframePills,
} from "../components/charts";
import { Card, EmptyState, ErrorState, LoadingState, ProgressBar } from "../components/ui";
import { usePoll } from "../hooks/usePoll";
import { formatBytes, formatPercent, formatRate, formatUptime } from "../lib/format";
import type { NodeInfo, NodeMetricPoint, Timeframe } from "../types";

export function NodePage() {
  const [timeframe, setTimeframe] = useState<Timeframe>("hour");
  const node = usePoll(() => api.get<NodeInfo>("/api/node"), 10000);
  const metrics = usePoll(
    () => api.get<NodeMetricPoint[]>(`/api/node/metrics?timeframe=${timeframe}&cf=AVERAGE`),
    60000,
  );

  if (node.loading) return <LoadingState />;
  if (node.error && !node.data) return <ErrorState message={node.error} />;

  const info = node.data;
  const points = metrics.data ?? [];
  const st = info?.status;
  const loadavg = Array.isArray(st?.loadavg) ? st.loadavg.join(" ") : null;
  // Backend now normalizes /api/node so flat mem/maxmem/maxcpu are always present,
  // but we keep fallback for older deploys or direct PVE shapes.
  const memUsed = st?.mem ?? st?.memory?.used ?? null;
  const memTotal = st?.maxmem ?? st?.memory?.total ?? null;
  const cores = st?.maxcpu ?? st?.cpuinfo?.cpus ?? null;

  const pct0 = (v: number) => formatPercent(v, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg">
          Node <span className="text-cyan">{info?.name ?? "?"}</span>
        </h1>
        {st && (
          <div className="text-xs text-muted metric">
            up {formatUptime(st.uptime)}
            {loadavg && <> · load {loadavg}</>}
          </div>
        )}
      </div>

      {node.error && <ErrorState message={node.error} />}

      {st && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Card title="CPU">
            <div className="text-sm metric mb-2">
              {formatPercent(st.cpu)}{" "}
              {cores != null && <span className="text-muted">of {cores} cores</span>}
            </div>
            <ProgressBar fraction={st.cpu} />
          </Card>
          <Card title="RAM">
            <div className="text-sm metric mb-2">
              {formatBytes(memUsed)} <span className="text-muted">/ {formatBytes(memTotal)}</span>
            </div>
            <ProgressBar fraction={memTotal ? (memUsed ?? 0) / memTotal : 0} />
          </Card>
        </div>
      )}

      <Card title="Storage">
        {!info || info.storage.length === 0 ? (
          <EmptyState message="no storage info" />
        ) : (
          <div className="space-y-3">
            {info.storage.map((s) => {
              const f = s.total > 0 ? s.used / s.total : 0;
              return (
                <div key={s.storage} className="text-sm metric">
                  <div className="flex flex-wrap justify-between gap-1 mb-1">
                    <span>
                      {s.storage} <span className="text-muted text-xs">({s.type})</span>
                    </span>
                    <span className="text-muted">
                      {formatBytes(s.used)} / {formatBytes(s.total)} · {formatPercent(f, 0)}
                    </span>
                  </div>
                  <ProgressBar fraction={f} />
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <TimeframePills value={timeframe} onChange={setTimeframe} />
        {metrics.error && <span className="text-xs text-red">{metrics.error}</span>}
      </div>

      {metrics.loading ? (
        <LoadingState />
      ) : (
        <>
          <Card title="CPU %">
            <MetricAreaChart
              data={points}
              timeframe={timeframe}
              yFormat={pct0}
              yDomain={[0, 1]}
              series={[{ key: "cpu", name: "cpu", color: CHART.cyan, format: (v) => formatPercent(v) }]}
            />
          </Card>
          <Card title="IO wait">
            <MetricAreaChart
              data={points}
              timeframe={timeframe}
              yFormat={pct0}
              series={[
                { key: "iowait", name: "iowait", color: CHART.amber, format: (v) => formatPercent(v) },
              ]}
            />
          </Card>
          <Card title="RAM">
            <MetricAreaChart
              data={points}
              timeframe={timeframe}
              yFormat={formatBytes}
              series={[
                { key: "memused", name: "used", color: CHART.cyan, format: formatBytes },
                { key: "memtotal", name: "total", color: CHART.pink, format: formatBytes },
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
          <Card title="Root filesystem">
            <MetricAreaChart
              data={points}
              timeframe={timeframe}
              yFormat={formatBytes}
              series={[
                { key: "rootused", name: "used", color: CHART.cyan, format: formatBytes },
                { key: "roottotal", name: "total", color: CHART.pink, format: formatBytes },
              ]}
            />
          </Card>
        </>
      )}
    </div>
  );
}
