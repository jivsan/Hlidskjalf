import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import { CHART, Sparkline } from "../../components/charts";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../components/Toast";
import { Card, EmptyState, ProgressBar } from "../../components/ui";
import { usePoll } from "../../hooks/usePoll";
import { formatBytes, formatPercent, formatRate } from "../../lib/format";
import { taskResultMessage, watchTask } from "../../lib/tasks";
import type { TemplateInfo, VmDetail, VmMetricPoint } from "../../types";

export function OverviewTab({
  vm,
  onChanged,
  isAdmin = false,
}: {
  vm: VmDetail;
  onChanged: () => void;
  /** Reinstall/destroy are admin-only server-side; hide the dead UI otherwise. */
  isAdmin?: boolean;
}) {
  const metrics = usePoll(
    () => api.get<VmMetricPoint[]>(`/api/vms/${vm.vmid}/metrics?timeframe=hour&cf=AVERAGE`),
    30000,
  );
  const points = metrics.data ?? [];

  const diskFraction = vm.maxdisk > 0 ? vm.disk / vm.maxdisk : null;
  const bwTotal = vm.netin + vm.netout;
  const inShare = bwTotal > 0 ? vm.netin / bwTotal : 0.5;

  const cfg = vm.config;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Disk Usage">
          {vm.maxdisk > 0 ? (
            <div className="space-y-2 metric">
              <div className="flex justify-between text-sm">
                <span>{diskFraction != null ? formatPercent(diskFraction) : "—"}</span>
                <span className="text-muted">
                  {formatBytes(vm.disk)} / {formatBytes(vm.maxdisk)}
                </span>
              </div>
              <ProgressBar fraction={diskFraction ?? 0} />
              {vm.kind === "qemu" && vm.disk === 0 && (
                <p className="text-xs text-muted">
                  usage inside the guest requires the QEMU agent — allocated size shown
                </p>
              )}
            </div>
          ) : (
            <EmptyState message="no disk info" />
          )}
        </Card>

        <Card
          title="Bandwidth (since boot)"
          actions={
            <Link to={`/vm/${vm.vmid}?tab=graphs&sub=bandwidth`} className="text-xs text-cyan hover:underline">
              graphs →
            </Link>
          }
        >
          <div className="space-y-2 metric">
            <div className="flex justify-between text-sm">
              <span>
                <span style={{ color: CHART.cyan }}>▮</span> in {formatBytes(vm.netin)}
              </span>
              <span>
                <span style={{ color: CHART.pink }}>▮</span> out {formatBytes(vm.netout)}
              </span>
            </div>
            {bwTotal > 0 ? (
              <div className="h-2 rounded-full overflow-hidden flex bg-border-token/40">
                <div className="h-full bg-cyan" style={{ width: `${inShare * 100}%` }} />
                <div className="h-full w-0.5 bg-surface shrink-0" />
                <div className="h-full bg-pink flex-1" />
              </div>
            ) : (
              <div className="h-2 rounded-full bg-border-token/40" />
            )}
            <div className="text-xs text-muted">total {formatBytes(bwTotal)}</div>
          </div>
        </Card>

        <Card title="CPU — last hour">
          <Sparkline
            data={points}
            series={[
              {
                key: "cpu",
                name: "cpu",
                color: CHART.cyan,
                format: (v) => formatPercent(v),
              },
            ]}
            height={64}
          />
        </Card>

        <Card title="Network — last hour">
          <Sparkline
            data={points}
            series={[
              { key: "netin", name: "in", color: CHART.cyan, format: formatRate },
              { key: "netout", name: "out", color: CHART.pink, format: formatRate },
            ]}
            height={64}
          />
        </Card>
      </div>

      <Card title="Config">
        <dl className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm metric">
          <ConfigItem label="cores" value={cfg.cores != null ? String(cfg.cores) : "—"} />
          <ConfigItem
            label="memory"
            value={cfg.memory != null ? formatBytes(cfg.memory * 1024 * 1024) : "—"}
          />
          <ConfigItem label="vlan" value={vm.vlan ?? "—"} />
          <ConfigItem label="mac" value={vm.mac ?? "—"} />
          <ConfigItem label="bridge" value={vm.bridge ?? "—"} />
          <ConfigItem label="boot" value={cfg.boot ?? "—"} />
          <ConfigItem
            label="onboot"
            value={cfg.onboot === 1 || cfg.onboot === true ? "yes" : "no"}
          />
          <ConfigItem label="ostype" value={cfg.ostype ?? "—"} />
          <ConfigItem label="agent" value={vm.agent ? "connected" : "not available"} />
        </dl>
        {cfg.description && (
          <p className="mt-3 text-xs text-muted whitespace-pre-wrap">{cfg.description}</p>
        )}
      </Card>

      {isAdmin && <DangerZone vm={vm} onChanged={onChanged} />}
    </div>
  );
}

function ConfigItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="break-all">{value}</dd>
    </div>
  );
}

// --- Danger zone: reinstall + destroy ---

function DangerZone({ vm, onChanged }: { vm: VmDetail; onChanged: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<"reinstall" | "destroy" | null>(null);
  const [templateVmid, setTemplateVmid] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const templates = usePoll(() => api.get<TemplateInfo[]>("/api/templates"), 300000);

  const runUpids = async (upids: string[], label: string) => {
    for (const upid of upids) {
      const st = await watchTask(upid);
      const { ok, message } = taskResultMessage(label, st);
      if (!ok) {
        toast.error(message);
        return false;
      }
    }
    toast.success(`${label}: OK`);
    return true;
  };

  const doReinstall = async (confirmName: string) => {
    if (templateVmid == null) return;
    setBusy(true);
    try {
      const res = await api.post<{ vmid: number; upids: string[] }>(
        `/api/vms/${vm.vmid}/reinstall`,
        { template_vmid: templateVmid, confirm_name: confirmName },
      );
      setDialog(null);
      await runUpids(res.upids, `reinstall ${vm.name}`);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "reinstall failed");
    } finally {
      setBusy(false);
    }
  };

  const doDestroy = async (confirmName: string) => {
    setBusy(true);
    try {
      const res = await api.del<{ upids: string[] }>(`/api/vms/${vm.vmid}`, {
        confirm_name: confirmName,
      });
      setDialog(null);
      await runUpids(res.upids, `destroy ${vm.name}`);
      navigate("/");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "destroy failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={<span className="text-red">Danger zone</span>} className="border-red/40">
      {vm.protected ? (
        <p className="text-sm text-muted">
          This VM is <span className="text-fg">protected</span> — reinstall and destroy are
          refused server-side. Remove it from HLIDSKJALF_PROTECTED_VMIDS to enable
          destructive actions.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="input w-auto"
              value={templateVmid ?? ""}
              onChange={(e) => setTemplateVmid(e.target.value ? Number(e.target.value) : null)}
              aria-label="reinstall template"
            >
              <option value="">select template…</option>
              {(templates.data ?? []).map((t) => (
                <option key={t.vmid} value={t.vmid}>
                  {t.name} ({t.vmid})
                </option>
              ))}
            </select>
            <button
              className="btn-amber"
              disabled={templateVmid == null}
              onClick={() => setDialog("reinstall")}
            >
              reinstall
            </button>
            <span className="text-xs text-muted">
              wipes the disk, re-clones the template, keeps VMID / MAC / IP
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-red" onClick={() => setDialog("destroy")}>
              destroy
            </button>
            <span className="text-xs text-muted">permanently deletes the guest and its disks</span>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={dialog === "reinstall"}
        title={`Reinstall ${vm.name}`}
        confirmLabel="reinstall"
        confirmClass="btn-amber"
        requireText={vm.name}
        busy={busy}
        onConfirm={(t) => void doReinstall(t)}
        onCancel={() => setDialog(null)}
      >
        <p>
          The disk of <span className="text-fg">{vm.name}</span> will be destroyed and re-cloned
          from the selected template. VMID, MAC and IP are preserved. This cannot be undone.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "destroy"}
        title={`Destroy ${vm.name}`}
        confirmLabel="destroy forever"
        confirmClass="btn-red"
        requireText={vm.name}
        busy={busy}
        onConfirm={(t) => void doDestroy(t)}
        onCancel={() => setDialog(null)}
      >
        <p>
          <span className="text-red">{vm.name}</span> (vmid {vm.vmid}) and all of its disks will
          be permanently destroyed. This cannot be undone.
        </p>
      </ConfirmDialog>

    </Card>
  );
}
