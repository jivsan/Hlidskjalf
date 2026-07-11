import { useState } from "react";
import { api } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../components/Toast";
import { Card } from "../../components/ui";
import type { VmDetail } from "../../types";

export function RescueTab({ vm, onChanged }: { vm: VmDetail; onChanged: () => void }) {
  const toast = useToast();
  const [dialog, setDialog] = useState<"enter" | "exit" | null>(null);
  const [busy, setBusy] = useState(false);

  const enter = async () => {
    setBusy(true);
    try {
      await api.post<{ rescue: boolean }>(`/api/vms/${vm.vmid}/rescue`, {});
      toast.success(`${vm.name}: rescue mode enabled — rebooting into SystemRescue`);
      setDialog(null);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "enter rescue failed");
    } finally {
      setBusy(false);
    }
  };

  const exit = async () => {
    setBusy(true);
    try {
      await api.del<{ rescue: boolean }>(`/api/vms/${vm.vmid}/rescue`);
      toast.success(`${vm.name}: rescue mode disabled — rebooting from disk`);
      setDialog(null);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "exit rescue failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Rescue mode" className={vm.rescue ? "border-amber/70" : ""}>
      <div className="space-y-4 text-sm">
        <p className="text-muted">
          Rescue mode reboots the guest from a SystemRescue ISO while leaving its disks
          untouched. Use it to repair a broken bootloader, reset passwords, or inspect a
          filesystem offline. The original boot order is stored and restored on exit.
        </p>
        <div className="metric">
          <span className="text-muted">current state: </span>
          {vm.rescue ? (
            <span className="text-amber">
              RESCUE MODE{vm.rescue_since ? ` (since ${vm.rescue_since})` : ""}
            </span>
          ) : (
            <span className="text-cyan">normal boot</span>
          )}
        </div>
        <div>
          {vm.rescue ? (
            <button className="btn-amber" onClick={() => setDialog("exit")}>
              exit rescue
            </button>
          ) : (
            <button className="btn-amber" onClick={() => setDialog("enter")}>
              enter rescue
            </button>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={dialog === "enter"}
        title={`Enter rescue mode — ${vm.name}`}
        confirmLabel="enter rescue"
        confirmClass="btn-amber"
        busy={busy}
        onConfirm={() => void enter()}
        onCancel={() => setDialog(null)}
      >
        <p>
          The guest will reboot from the rescue ISO. Anything running inside it will be
          interrupted. Its disks are not modified.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "exit"}
        title={`Exit rescue mode — ${vm.name}`}
        confirmLabel="exit rescue"
        confirmClass="btn-amber"
        busy={busy}
        onConfirm={() => void exit()}
        onCancel={() => setDialog(null)}
      >
        <p>The original boot order will be restored and the guest rebooted from its disk.</p>
      </ConfirmDialog>
    </Card>
  );
}
