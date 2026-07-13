import { api } from "../api";
import type { TaskStatus } from "../types";

// Stop watching a task after this long; the task keeps running server-side,
// we just stop holding a spinner open for it.
const WATCH_MAX_MS = 15 * 60 * 1000;

/**
 * Poll a PVE task UPID every second until it stops (or the watch times out).
 * Resolves with the final TaskStatus; rejects on fetch errors/timeouts.
 */
export function watchTask(upid: string): Promise<TaskStatus> {
  const deadline = Date.now() + WATCH_MAX_MS;
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const st = await api.get<TaskStatus>(`/api/tasks/${encodeURIComponent(upid)}/status`);
        if (st.status === "stopped") {
          resolve(st);
        } else if (Date.now() > deadline) {
          reject(new Error("task still running after 15 min — check the Tasks tab"));
        } else {
          window.setTimeout(poll, 1000);
        }
      } catch (e) {
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    };
    void poll();
  });
}

/** Human label for a task type + result toast text. */
export function taskResultMessage(label: string, st: TaskStatus): {
  ok: boolean;
  message: string;
} {
  const ok = st.exitstatus === "OK";
  return {
    ok,
    message: ok ? `${label}: OK` : `${label}: ${st.exitstatus ?? "failed"}`,
  };
}
