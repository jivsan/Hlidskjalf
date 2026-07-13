import { useMemo, useState, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, getNodeName } from "../api";
import { useToast } from "../components/Toast";
import { EmptyState, ErrorState, KindBadge, LoadingState, PageHeader, StatusDot } from "../components/ui";
import { usePoll } from "../hooks/usePoll";
import { formatBytes, formatPercent, formatUptime } from "../lib/format";
import { taskResultMessage, watchTask } from "../lib/tasks";
import type { BandwidthSummary, VmListItem } from "../types";

type SortKey = "vmid" | "name" | "status" | "cpu" | "mem" | "uptime" | "traffic";

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function Fleet() {
  const navigate = useNavigate();
  const toast = useToast();
  const [sortKey, setSortKey] = useState<SortKey>("vmid");
  const [sortAsc, setSortAsc] = useState(true);
  const [busyVmids, setBusyVmids] = useState<Set<number>>(new Set());

  const vms = usePoll(() => api.get<VmListItem[]>("/api/vms"), 5000);
  const summary = usePoll(
    () => api.get<BandwidthSummary>(`/api/bandwidth/summary?month=${currentMonth()}`),
    60000,
  );

  const traffic = (vmid: number): number | null => {
    const rec = summary.data?.vms[String(vmid)];
    return rec ? rec.total ?? rec.bytes_in + rec.bytes_out : null;
  };

  const sorted = useMemo(() => {
    const list = [...(vms.data ?? [])];
    const dir = sortAsc ? 1 : -1;
    const val = (v: VmListItem): number | string => {
      switch (sortKey) {
        case "vmid":
          return v.vmid;
        case "name":
          return v.name;
        case "status":
          return v.status;
        case "cpu":
          return v.maxcpu > 0 ? v.cpu / v.maxcpu : 0;
        case "mem":
          return v.mem;
        case "uptime":
          return v.uptime;
        case "traffic":
          return traffic(v.vmid) ?? -1;
      }
    };
    list.sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (typeof av === "string" || typeof bv === "string") {
        return String(av).localeCompare(String(bv)) * dir;
      }
      return (av - bv) * dir;
    });
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vms.data, summary.data, sortKey, sortAsc]);

  const setSort = (k: SortKey) => {
    if (k === sortKey) setSortAsc((a) => !a);
    else {
      setSortKey(k);
      setSortAsc(true);
    }
  };

  const quickAction = async (e: MouseEvent, vm: VmListItem, action: "start" | "shutdown") => {
    e.stopPropagation();
    setBusyVmids((s) => new Set(s).add(vm.vmid));
    try {
      const { upid } = await api.post<{ upid: string }>(
        `/api/vms/${vm.vmid}/status/${action}`,
      );
      const st = await watchTask(upid);
      const { ok, message } = taskResultMessage(`${action} ${vm.name}`, st);
      if (ok) toast.success(message);
      else toast.error(message);
      vms.refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      setBusyVmids((s) => {
        const next = new Set(s);
        next.delete(vm.vmid);
        return next;
      });
    }
  };

  if (vms.loading) return <LoadingState />;
  if (vms.error && !vms.data) return <ErrorState message={vms.error} />;

  const list = vms.data ?? [];
  const running = list.filter((v) => v.status === "running").length;
  const aggTraffic = summary.data
    ? Object.values(summary.data.vms).reduce(
        (acc, v) => acc + (v.total ?? v.bytes_in + v.bytes_out),
        0,
      )
    : null;

  const Th = ({ k, children, className = "" }: { k: SortKey; children: string; className?: string }) => (
    <th
      className={`px-3 py-2 text-left text-[11px] font-medium text-muted uppercase tracking-eyebrow cursor-pointer select-none whitespace-nowrap hover:text-fg ${className}`}
      onClick={() => setSort(k)}
      aria-sort={sortKey === k ? (sortAsc ? "ascending" : "descending") : "none"}
    >
      {children}
      {sortKey === k && <span className="ml-1 text-pink">{sortAsc ? "▲" : "▼"}</span>}
    </th>
  );

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow={`guests on ${getNodeName() || "this node"}`}
        title="Fleet"
        sub={
          <>
            <span className="metric">
              <span className="text-cyan">{running}</span>/{list.length}
            </span>{" "}
            running
            {aggTraffic != null && (
              <>
                {" · "}
                <span className="metric text-fg">{formatBytes(aggTraffic)}</span> this month
              </>
            )}
          </>
        }
      />

      {vms.error && <ErrorState message={vms.error} />}

      {/* Nothing is guarded unless HLIDSKJALF_PROTECTED_VMIDS says so — and the
          default is empty, so a fresh deployment can destroy the VM running this
          very panel. The fleet already knows which guests are protected, so this
          needs no extra API call. */}
      {list.length > 0 && !list.some((v) => v.protected) && (
        <div
          className="card border-amber/50 bg-amber/5 px-4 py-3 text-sm text-amber"
          role="alert"
        >
          <span className="font-medium">No guest is protected.</span>{" "}
          <span className="text-fg">
            Destructive actions are allowed on every VM here — including the one running
            this panel. Set{" "}
            <span className="metric text-amber">HLIDSKJALF_PROTECTED_VMIDS</span> to the
            VMIDs you cannot afford to lose, then restart.
          </span>
        </div>
      )}

      {list.length === 0 ? (
        <EmptyState message={`no guests found on ${getNodeName() || "this node"}`} />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border-token">
              <tr>
                <Th k="status">St</Th>
                <Th k="name">Name</Th>
                <Th k="vmid">VMID</Th>
                <Th k="cpu">CPU</Th>
                <Th k="mem">RAM</Th>
                <Th k="uptime">Uptime</Th>
                <Th k="traffic">Traffic (mo)</Th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((vm) => {
                const busy = busyVmids.has(vm.vmid);
                const runningVm = vm.status === "running";
                const tr = traffic(vm.vmid);
                return (
                  <tr
                    key={vm.vmid}
                    className="border-b border-border-token/60 last:border-0 cursor-pointer transition-colors hover:bg-surface-2"
                    onClick={() => navigate(`/vm/${vm.vmid}`)}
                  >
                    <td className="px-3 py-2">
                      <StatusDot status={vm.status} />
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className="text-fg">{vm.name}</span>{" "}
                      <KindBadge kind={vm.kind} />
                      {vm.rescue && <span className="ml-1 text-[10px] text-amber">RESCUE</span>}
                      {vm.protected && (
                        <span className="ml-1 text-[10px] text-muted" title="protected">
                          ⛨
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 metric text-muted">{vm.vmid}</td>
                    <td className="px-3 py-2 whitespace-nowrap metric">
                      {runningVm && vm.maxcpu > 0 ? formatPercent(vm.cpu / vm.maxcpu) : "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap metric">
                      {runningVm ? (
                        <>
                          {formatBytes(vm.mem)}{" "}
                          <span className="text-muted">/ {formatBytes(vm.maxmem)}</span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap metric">
                      {runningVm ? formatUptime(vm.uptime) : "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap metric">
                      {tr != null ? formatBytes(tr) : "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-right">
                      {runningVm ? (
                        <button
                          className="btn-plain px-2 py-0.5 text-xs"
                          disabled={busy || vm.protected}
                          title={
                            vm.protected
                              ? "protected VM — shutdown from its page"
                              : "shutdown"
                          }
                          aria-label={`shutdown ${vm.name}`}
                          onClick={(e) => quickAction(e, vm, "shutdown")}
                        >
                          ⏻
                        </button>
                      ) : (
                        <button
                          className="btn-cyan px-2 py-0.5 text-xs"
                          disabled={busy}
                          title="start"
                          aria-label={`start ${vm.name}`}
                          onClick={(e) => quickAction(e, vm, "start")}
                        >
                          ▶
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
