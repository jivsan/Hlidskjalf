import { debug } from "../api";
import { Card, EmptyState, ErrorState, LoadingState } from "../components/ui";
import { usePoll } from "../hooks/usePoll";

function formatTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

export function Debug() {
  const health = usePoll(() => debug.getHealth(), 10000);
  const config = usePoll(() => debug.getConfig(), 30000);
  const logs = usePoll(() => debug.getLogs(), 10000);
  const errors = usePoll(() => debug.getErrors(), 10000);
  const acc = usePoll(() => debug.getAccumulator(), 15000);

  const doRefresh = (p: { refresh: () => void }) => {
    p.refresh();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Debug</h1>
        <p className="text-muted text-sm">Admin-only diagnostics and internal state. HLIDSKJALF_DEBUG must be enabled.</p>
      </div>

      {health.data?.debug && (
        <div className="card border-pink/40 p-3 text-sm text-pink">
          Debug mode active (HLIDSKJALF_DEBUG)
        </div>
      )}

      {/* System Health */}
      <Card
        title="System Health"
        actions={
          <button className="btn-plain text-xs" onClick={() => doRefresh(health)}>
            refresh
          </button>
        }
      >
        {health.loading && !health.data ? (
          <LoadingState />
        ) : health.error ? (
          <ErrorState message={health.error} />
        ) : health.data ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-sm font-mono">
            {Object.entries(health.data).map(([k, v]) => (
              <div key={k}>
                <span className="text-muted">{k}:</span> {String(v)}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState message="No health data" />
        )}
      </Card>

      {/* Config (redacted) */}
      <Card
        title="Config (redacted)"
        actions={
          <button className="btn-plain text-xs" onClick={() => doRefresh(config)}>
            refresh
          </button>
        }
      >
        {config.loading && !config.data ? (
          <LoadingState />
        ) : config.error ? (
          <ErrorState message={config.error} />
        ) : config.data ? (
          <div className="max-h-80 overflow-auto text-xs font-mono space-y-1">
            {Object.entries(config.data)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <span className="text-muted w-48 shrink-0">{k}</span>
                  <span className="break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
                </div>
              ))}
          </div>
        ) : (
          <EmptyState message="No config" />
        )}
      </Card>

      {/* Accumulator */}
      <Card
        title="Accumulator Status"
        actions={
          <button className="btn-plain text-xs" onClick={() => doRefresh(acc)}>
            refresh
          </button>
        }
      >
        {acc.loading && !acc.data ? (
          <LoadingState />
        ) : acc.error ? (
          <ErrorState message={acc.error} />
        ) : acc.data ? (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-muted">running:</span>{" "}
              <span className={acc.data.running ? "text-cyan" : "text-red"}>{String(acc.data.running)}</span>
            </div>
            <div>
              <span className="text-muted">prev_count:</span> {acc.data.prev_count}
            </div>
            {acc.data.task_name && (
              <div className="col-span-2">
                <span className="text-muted">task:</span> {acc.data.task_name}
              </div>
            )}
          </div>
        ) : (
          <EmptyState message="No accumulator data" />
        )}
      </Card>

      {/* Recent Logs */}
      <Card
        title="Recent Logs (last ~50)"
        actions={
          <button className="btn-plain text-xs" onClick={() => doRefresh(logs)}>
            refresh
          </button>
        }
      >
        {logs.loading && !logs.data ? (
          <LoadingState />
        ) : logs.error ? (
          <ErrorState message={logs.error} />
        ) : logs.data && logs.data.length > 0 ? (
          <div className="max-h-96 overflow-auto text-xs font-mono">
            <table className="w-full">
              <tbody>
                {[...logs.data].reverse().map((l, i) => (
                  <tr key={i} className="border-b border-border-token/50 last:border-0">
                    <td className="py-0.5 pr-2 text-muted whitespace-nowrap">{formatTs(l.ts)}</td>
                    <td className={`py-0.5 pr-2 ${l.level === "ERROR" ? "text-red" : l.level === "WARNING" ? "text-amber-400" : "text-cyan"}`}>{l.level}</td>
                    <td className="py-0.5 pr-2 text-muted">{l.logger}</td>
                    <td className="py-0.5 break-all">{l.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState message="No recent logs" />
        )}
        <div className="text-[10px] text-muted mt-2">Buffered in-memory (up to 100). Enable DEBUG for more detail.</div>
      </Card>

      {/* Recent Errors */}
      <Card
        title="Recent Errors (last ~50)"
        actions={
          <button className="btn-plain text-xs" onClick={() => doRefresh(errors)}>
            refresh
          </button>
        }
      >
        {errors.loading && !errors.data ? (
          <LoadingState />
        ) : errors.error ? (
          <ErrorState message={errors.error} />
        ) : errors.data && errors.data.length > 0 ? (
          <div className="max-h-96 overflow-auto text-xs font-mono">
            <table className="w-full">
              <tbody>
                {[...errors.data].reverse().map((e, i) => (
                  <tr key={i} className="border-b border-border-token/50 last:border-0 align-top">
                    <td className="py-0.5 pr-2 text-muted whitespace-nowrap">{formatTs(e.ts)}</td>
                    <td className="py-0.5 pr-2 text-cyan">{e.method}</td>
                    <td className="py-0.5 pr-2 text-red break-all">{e.path}</td>
                    <td className="py-0.5 break-all">{e.error}</td>
                    {e.traceback && (
                      <td className="py-0.5">
                        <details>
                          <summary className="cursor-pointer text-muted">trace</summary>
                          <pre className="whitespace-pre-wrap text-[10px] text-red mt-1">{e.traceback}</pre>
                        </details>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState message="No recent errors" />
        )}
      </Card>
    </div>
  );
}
