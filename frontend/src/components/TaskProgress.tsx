import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { TaskStatus } from "../types";

interface TaskRow {
  upid: string;
  state: "running" | "ok" | "error";
  detail?: string;
}

/** Live progress list for a set of PVE task UPIDs (polls each every 1s). */
export function TaskProgress({
  upids,
  onAllDone,
}: {
  upids: string[];
  onAllDone?: (allOk: boolean) => void;
}) {
  const [rows, setRows] = useState<TaskRow[]>(() =>
    upids.map((upid) => ({ upid, state: "running" as const })),
  );
  const doneRef = useRef(false);

  useEffect(() => {
    setRows(upids.map((upid) => ({ upid, state: "running" as const })));
    doneRef.current = false;
    if (upids.length === 0) return;

    let cancelled = false;
    const timers: number[] = [];

    upids.forEach((upid, idx) => {
      const poll = async () => {
        if (cancelled) return;
        try {
          const st = await api.get<TaskStatus>(
            `/api/tasks/${encodeURIComponent(upid)}/status`,
          );
          if (cancelled) return;
          if (st.status === "stopped") {
            setRows((rs) => {
              const next = [...rs];
              next[idx] = {
                upid,
                state: st.exitstatus === "OK" ? "ok" : "error",
                detail: st.exitstatus,
              };
              return next;
            });
          } else {
            timers.push(window.setTimeout(poll, 1000));
          }
        } catch (e) {
          if (cancelled) return;
          setRows((rs) => {
            const next = [...rs];
            next[idx] = {
              upid,
              state: "error",
              detail: e instanceof Error ? e.message : String(e),
            };
            return next;
          });
        }
      };
      void poll();
    });

    return () => {
      cancelled = true;
      timers.forEach((t) => window.clearTimeout(t));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(upids)]);

  useEffect(() => {
    if (doneRef.current) return;
    if (rows.length > 0 && rows.every((r) => r.state !== "running")) {
      doneRef.current = true;
      onAllDone?.(rows.every((r) => r.state === "ok"));
    }
  }, [rows, onAllDone]);

  return (
    <ul className="space-y-1 text-xs metric">
      {rows.map((r) => (
        <li key={r.upid} className="flex items-center gap-2">
          {r.state === "running" && <span className="text-amber animate-pulse">●</span>}
          {r.state === "ok" && <span className="text-cyan">●</span>}
          {r.state === "error" && <span className="text-red">●</span>}
          <span className="truncate text-muted" title={r.upid}>
            {r.upid}
          </span>
          <span
            className={
              r.state === "ok" ? "text-cyan" : r.state === "error" ? "text-red" : "text-amber"
            }
          >
            {r.state === "running" ? "running…" : r.detail ?? r.state}
          </span>
        </li>
      ))}
    </ul>
  );
}
