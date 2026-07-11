import { useMemo, useState } from "react";
import { api } from "../../api";
import { Card, EmptyState, ErrorState, LoadingState } from "../../components/ui";
import { usePoll } from "../../hooks/usePoll";
import { formatDateTime, formatDuration } from "../../lib/format";
import type { RecentTask } from "../../types";

function exitChip(task: RecentTask) {
  if (task.endtime == null) {
    return <span className="text-amber">running</span>;
  }
  if (task.status === "OK") {
    return <span className="text-cyan">OK</span>;
  }
  return <span className="text-red">{task.status ?? "?"}</span>;
}

export function TasksTab({ vmid }: { vmid: number }) {
  const [showAll, setShowAll] = useState(false);
  const tasks = usePoll(() => api.get<RecentTask[]>("/api/tasks/recent"), 10000);

  const filtered = useMemo(() => {
    const list = tasks.data ?? [];
    return showAll ? list : list.filter((t) => t.id === String(vmid));
  }, [tasks.data, showAll, vmid]);

  if (tasks.loading) return <LoadingState />;
  if (tasks.error && !tasks.data) return <ErrorState message={tasks.error} />;

  return (
    <Card
      title="Recent tasks"
      actions={
        <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
            className="accent-[#ff4fa3]"
          />
          show all guests
        </label>
      }
    >
      {tasks.error && <ErrorState message={tasks.error} />}
      {filtered.length === 0 ? (
        <EmptyState
          message={showAll ? "no recent tasks" : `no recent tasks for vmid ${vmid}`}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs metric">
            <thead className="border-b border-border-token text-muted uppercase tracking-wider">
              <tr>
                <th className="px-2 py-2 text-left">Type</th>
                {showAll && <th className="px-2 py-2 text-left">ID</th>}
                <th className="px-2 py-2 text-left">User</th>
                <th className="px-2 py-2 text-left">Start</th>
                <th className="px-2 py-2 text-left">Duration</th>
                <th className="px-2 py-2 text-left">Exit</th>
                <th className="px-2 py-2 text-left">UPID</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.upid} className="border-b border-border-token/50 last:border-0">
                  <td className="px-2 py-1.5 whitespace-nowrap">{t.type}</td>
                  {showAll && <td className="px-2 py-1.5">{t.id}</td>}
                  <td className="px-2 py-1.5 whitespace-nowrap">{t.user}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{formatDateTime(t.starttime)}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {t.endtime != null ? formatDuration(t.starttime, t.endtime) : "—"}
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{exitChip(t)}</td>
                  <td
                    className="px-2 py-1.5 text-muted max-w-[16rem] truncate"
                    title={t.upid}
                  >
                    {t.upid}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
