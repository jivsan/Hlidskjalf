import { lazy, Suspense, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useToast } from "../components/Toast";
import { ErrorState, LoadingState } from "../components/ui";
import { usePoll } from "../hooks/usePoll";
import { formatUptime } from "../lib/format";
import { taskResultMessage, watchTask } from "../lib/tasks";
import type { PowerAction, VmDetail } from "../types";

// noVNC is heavy — load it only when the console tab is opened.
const ConsoleTab = lazy(() =>
  import("./vm/ConsoleTab").then((m) => ({ default: m.ConsoleTab })),
);
import { GraphsTab } from "./vm/GraphsTab";
import { OverviewTab } from "./vm/OverviewTab";
import { RescueTab } from "./vm/RescueTab";
import { TasksTab } from "./vm/TasksTab";

const TABS = ["overview", "graphs", "console", "rescue", "tasks"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABELS: Record<Tab, string> = {
  overview: "Overview",
  graphs: "Graphs",
  console: "Console",
  rescue: "Rescue",
  tasks: "Tasks & Logs",
};

export function VmDetailPage({ currentRole: _role, myVmid: _myVmid }: { currentRole?: string; myVmid?: number | null }) {
  const { vmid: vmidParam } = useParams<{ vmid: string }>();
  const vmid = vmidParam;
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();
  const [busyAction, setBusyAction] = useState<PowerAction | null>(null);

  const rawTab = searchParams.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(rawTab ?? "")
    ? (rawTab as Tab)
    : "overview";
  const setTab = (t: Tab) => setSearchParams(t === "overview" ? {} : { tab: t }, { replace: true });

  // Slow the detail poll right down while the console tab is active (keep last
  // data, avoid churn) — but still fetch at least once, so a direct load of
  // ?tab=console isn't stuck on the loading spinner forever.
  const detail = usePoll(
    () => api.get<VmDetail>(`/api/vms/${vmid}`),
    tab === "console" ? 60000 : 3000,
  );

  const doAction = async (action: PowerAction) => {
    if (!detail.data) return;
    setBusyAction(action);
    try {
      const { upid } = await api.post<{ upid: string }>(`/api/vms/${vmid}/status/${action}`);
      const st = await watchTask(upid);
      const { ok, message } = taskResultMessage(`${action} ${detail.data.name}`, st);
      if (ok) toast.success(message);
      else toast.error(message);
      detail.refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      setBusyAction(null);
    }
  };

  const copyIp = async (ip: string) => {
    try {
      await navigator.clipboard.writeText(ip);
      toast.success(`copied ${ip}`);
    } catch {
      toast.error("clipboard unavailable");
    }
  };

  if (detail.loading) return <LoadingState />;
  if (!detail.data) return <ErrorState message={detail.error ?? "VM not found"} />;

  const vm = detail.data;
  const running = vm.status === "running";
  const ip = vm.ips.length > 0 ? vm.ips[0] : null;

  return (
    <div className="space-y-4">
      {/* systemd-unit-style status chip */}
      <div className="card px-4 py-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm metric">
        <span className={running ? "text-cyan" : vm.status === "stopped" ? "text-muted" : "text-red"}>
          ●
        </span>
        <span className="text-fg">{vm.name}</span>
        <span className="text-muted">—</span>
        <span className={running ? "text-cyan" : "text-red"}>
          {running ? "active (running)" : `inactive (${vm.status})`}
        </span>
        {ip && (
          <>
            <span className="text-muted">·</span>
            <button
              className="text-fg hover:text-cyan"
              title="copy IP"
              onClick={() => copyIp(ip)}
            >
              {ip}
            </button>
          </>
        )}
        {running && (
          <>
            <span className="text-muted">·</span>
            <span className="text-muted">up {formatUptime(vm.uptime)}</span>
          </>
        )}
        <span className="text-muted text-xs ml-auto">
          vmid {vm.vmid}
          {vm.protected && <span className="ml-2" title="protected">⛨ protected</span>}
        </span>
      </div>

      {vm.rescue && (
        <div className="card border-amber/70 bg-amber/10 px-4 py-3 text-amber text-sm" role="alert">
          ⚠ RESCUE MODE — this guest is booted from the rescue ISO
          {vm.rescue_since ? ` (since ${vm.rescue_since})` : ""}. Exit rescue from the Rescue tab.
        </div>
      )}

      {detail.error && <ErrorState message={detail.error} />}

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        <button
          className="btn-cyan"
          disabled={running || busyAction != null}
          onClick={() => doAction("start")}
        >
          {busyAction === "start" ? "starting…" : "start"}
        </button>
        <button
          className="btn-plain"
          disabled={!running || busyAction != null}
          onClick={() => doAction("shutdown")}
        >
          {busyAction === "shutdown" ? "shutting down…" : "shutdown"}
        </button>
        <button
          className="btn-plain"
          disabled={!running || busyAction != null}
          onClick={() => doAction("reboot")}
        >
          {busyAction === "reboot" ? "rebooting…" : "reboot"}
        </button>
        <button
          className="btn-red"
          disabled={!running || busyAction != null}
          title={vm.protected ? "protected VM — the server will refuse a hard stop" : "hard stop"}
          onClick={() => doAction("stop")}
        >
          {busyAction === "stop" ? "stopping…" : "stop"}
        </button>
        <button className="btn-plain" onClick={() => setTab("console")}>
          console
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-border-token flex gap-1 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${
              tab === t
                ? "border-pink text-pink"
                : "border-transparent text-muted hover:text-fg"
            }`}
            style={{ transition: "border-color 150ms ease, color 150ms ease" }}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab vm={vm} onChanged={detail.refresh} />}
      {tab === "graphs" && <GraphsTab vm={vm} />}
      {tab === "console" && (
        <Suspense fallback={<LoadingState message="loading console…" />}>
          <ConsoleTab vmid={vm.vmid} />
        </Suspense>
      )}
      {tab === "rescue" && <RescueTab vm={vm} onChanged={detail.refresh} />}
      {tab === "tasks" && <TasksTab vmid={vm.vmid} />}
    </div>
  );
}
