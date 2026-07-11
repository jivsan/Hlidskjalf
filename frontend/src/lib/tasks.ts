import { api } from "../api";
import type { TaskStatus } from "../types";

/**
 * Poll a PVE task UPID every second until it stops.
 * Resolves with the final TaskStatus; rejects on fetch errors.
 */
export function watchTask(upid: string): Promise<TaskStatus> {
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const st = await api.get<TaskStatus>(`/api/tasks/${encodeURIComponent(upid)}/status`);
        if (st.status === "stopped") {
          resolve(st);
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
