import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { TaskProgress } from "../components/TaskProgress";
import { useToast } from "../components/Toast";
import { Card, ErrorState, LoadingState, PageHeader } from "../components/ui";
import type { ProvisionDefaults, ProvisionRequest, TemplateInfo } from "../types";

const HOSTNAME_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const CIDR_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;

function validCidr(s: string): boolean {
  const m = CIDR_RE.exec(s);
  if (!m) return false;
  const octets = m.slice(1, 5).map(Number);
  const prefix = Number(m[5]);
  return octets.every((o) => o >= 0 && o <= 255) && prefix >= 1 && prefix <= 32;
}

interface Submitted {
  vmid: number;
  upids: string[];
}

/** What the VMID box says about what's currently typed in it. */
type VmidCheck =
  | { ok: true; auto: boolean; note: string }
  | { ok: false; note: string };

function checkVmid(raw: string, d: ProvisionDefaults): VmidCheck {
  const s = raw.trim();
  if (s === "") return { ok: true, auto: true, note: `auto — takes ${d.next_vmid}, the next free id` };
  if (!/^\d+$/.test(s)) return { ok: false, note: "digits only" };
  const vmid = Number(s);
  if (vmid < d.min_vmid || vmid > d.max_vmid)
    return { ok: false, note: `Proxmox VMIDs run from ${d.min_vmid} to ${d.max_vmid}` };
  if (d.protected_vmids.includes(vmid))
    return { ok: false, note: "protected — the panel refuses to clone onto this guest" };
  if (d.used_vmids.includes(vmid)) return { ok: false, note: "already in use on this node" };
  return { ok: true, auto: false, note: "free" };
}

export function Provision() {
  const toast = useToast();
  const [defaults, setDefaults] = useState<ProvisionDefaults | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [vmid, setVmid] = useState("");
  const [templateVmid, setTemplateVmid] = useState<number | "">("");
  const [cores, setCores] = useState(2);
  const [memoryMb, setMemoryMb] = useState(2048);
  const [diskGb, setDiskGb] = useState(20);
  const [vlan, setVlan] = useState("");
  const [ipCidr, setIpCidr] = useState("");
  const [gateway, setGateway] = useState("");
  const [sshKeys, setSshKeys] = useState("");
  const [start, setStart] = useState(true);

  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  const [tasksDone, setTasksDone] = useState<boolean | null>(null);

  useEffect(() => {
    Promise.all([
      api.get<ProvisionDefaults>("/api/provision/defaults"),
      api.get<TemplateInfo[]>("/api/templates"),
    ])
      .then(([d, t]) => {
        setDefaults(d);
        setTemplates(t);
        setSshKeys(d.default_ssh_keys);
        setVmid(String(d.next_vmid));
        if (d.vlans.length > 0) {
          setVlan(d.vlans[0]);
          setGateway(d.vlan_gateways[d.vlans[0]] ?? "");
        }
        if (t.length > 0) setTemplateVmid(t[0].vmid);
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  const onVlanChange = (v: string) => {
    setVlan(v);
    setGateway(defaults?.vlan_gateways[v] ?? "");
  };

  const nameOk = HOSTNAME_RE.test(name);
  const ipOk = validCidr(ipCidr);
  const vmidCheck = defaults ? checkVmid(vmid, defaults) : null;
  const formOk =
    nameOk && ipOk && (vmidCheck?.ok ?? false) && templateVmid !== "" && vlan !== "" &&
    cores >= 1 && memoryMb >= 128 && diskGb >= 1;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!formOk) return;
    setBusy(true);
    try {
      const body: ProvisionRequest = {
        name,
        template_vmid: templateVmid,
        // empty box = let the backend pick; it re-checks whatever we do send
        ...(vmid.trim() ? { vmid: Number(vmid.trim()) } : {}),
        cores,
        memory_mb: memoryMb,
        disk_gb: diskGb,
        vlan,
        ip_cidr: ipCidr,
        gateway,
        ssh_keys: sshKeys,
        start,
      };
      const res = await api.post<Submitted>("/api/vms", body);
      setSubmitted(res);
      setTasksDone(null);
      toast.success(`provisioning ${name} as vmid ${res.vmid}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "provision failed");
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <ErrorState message={loadError} />;
  if (!defaults || !templates) return <LoadingState />;

  if (submitted) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="new guest" title="Provision" />
        <Card title={`Creating ${name} (vmid ${submitted.vmid})`}>
          <TaskProgress
            upids={submitted.upids}
            onAllDone={(ok) => {
              setTasksDone(ok);
              if (ok) toast.success(`${name} created`);
              else toast.error(`${name}: some tasks failed`);
            }}
          />
          <div className="mt-4 flex gap-2">
            {tasksDone != null && (
              <Link to={`/vm/${submitted.vmid}`} className="btn-cyan">
                open {name} →
              </Link>
            )}
            <button
              className="btn-plain"
              onClick={() => {
                setSubmitted(null);
                setTasksDone(null);
              }}
            >
              provision another
            </button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader eyebrow="new guest" title="Provision" />
      {templates.length === 0 && (
        <ErrorState message="no templates found — create a cloud-init template on your Proxmox node first (VMID 9000+)" />
      )}
      <form onSubmit={submit} className="card p-5 space-y-6">
        {/* identity */}
        <div className="grid gap-4 md:grid-cols-3">
          <div>
            <label className="label" htmlFor="p-name">hostname</label>
            <input
              id="p-name"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder="vps-scratch-01"
              spellCheck={false}
              required
            />
            {name && !nameOk && (
              <p className="text-red text-xs mt-1">
                lowercase letters, digits and hyphens; must start/end alphanumeric
              </p>
            )}
          </div>
          <div>
            <label className="label" htmlFor="p-template">template</label>
            <select
              id="p-template"
              className="input"
              value={templateVmid}
              onChange={(e) => setTemplateVmid(e.target.value ? Number(e.target.value) : "")}
              required
            >
              {templates.map((t) => (
                <option key={t.vmid} value={t.vmid}>
                  {t.name} ({t.vmid})
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="flex items-baseline justify-between">
              <label className="label" htmlFor="p-vmid">VMID</label>
              {vmid.trim() !== String(defaults.next_vmid) && (
                <button
                  type="button"
                  className="text-xs text-cyan hover:underline"
                  onClick={() => setVmid(String(defaults.next_vmid))}
                >
                  next free
                </button>
              )}
            </div>
            <input
              id="p-vmid"
              className="input metric"
              value={vmid}
              onChange={(e) => setVmid(e.target.value)}
              placeholder={`${defaults.next_vmid} (auto)`}
              inputMode="numeric"
              spellCheck={false}
            />
            {vmidCheck && (
              <p
                className={`text-xs mt-1 ${
                  !vmidCheck.ok ? "text-red" : vmidCheck.auto ? "text-muted" : "text-cyan"
                }`}
              >
                {vmidCheck.note}
              </p>
            )}
          </div>
        </div>

        {/* resources */}
        <div className="space-y-3">
          <div className="eyebrow">resources</div>
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="label" htmlFor="p-cores">cores</label>
              <input
                id="p-cores"
                type="number"
                className="input metric"
                min={1}
                max={32}
                value={cores}
                onChange={(e) => setCores(Number(e.target.value))}
                required
              />
            </div>
            <div>
              <label className="label" htmlFor="p-mem">RAM (MB)</label>
              <input
                id="p-mem"
                type="number"
                className="input metric"
                min={128}
                step={128}
                value={memoryMb}
                onChange={(e) => setMemoryMb(Number(e.target.value))}
                required
              />
            </div>
            <div>
              <label className="label" htmlFor="p-disk">disk (GB)</label>
              <input
                id="p-disk"
                type="number"
                className="input metric"
                min={1}
                value={diskGb}
                onChange={(e) => setDiskGb(Number(e.target.value))}
                required
              />
            </div>
          </div>
        </div>

        {/* network */}
        <div className="space-y-3">
          <div className="eyebrow">network</div>
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="label" htmlFor="p-vlan">VLAN</label>
              {defaults.vlans.length === 0 ? (
                <p className="text-amber text-xs mt-1">
                  no VLANs configured —{" "}
                  <Link to="/settings" className="text-cyan hover:underline">
                    add them in settings
                  </Link>
                </p>
              ) : (
                <select
                  id="p-vlan"
                  className="input"
                  value={vlan}
                  onChange={(e) => onVlanChange(e.target.value)}
                  required
                >
                  {defaults.vlans.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div>
              <label className="label" htmlFor="p-ip">static IP (CIDR)</label>
              <input
                id="p-ip"
                className="input metric"
                value={ipCidr}
                onChange={(e) => setIpCidr(e.target.value.trim())}
                placeholder="192.168.20.50/24"
                spellCheck={false}
                required
              />
              {ipCidr && !ipOk && (
                <p className="text-red text-xs mt-1">expected x.x.x.x/nn</p>
              )}
            </div>
            <div>
              <label className="label" htmlFor="p-gw">gateway</label>
              <input
                id="p-gw"
                className="input metric"
                value={gateway}
                onChange={(e) => setGateway(e.target.value.trim())}
                placeholder="(may be empty)"
                spellCheck={false}
              />
            </div>
          </div>
        </div>

        {/* access */}
        <div className="space-y-3">
          <div className="eyebrow">access</div>
          <div>
            <label className="label" htmlFor="p-ssh">SSH authorized keys</label>
            <textarea
              id="p-ssh"
              className="input h-24 text-xs metric"
              value={sshKeys}
              onChange={(e) => setSshKeys(e.target.value)}
              spellCheck={false}
            />
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={start}
              onChange={(e) => setStart(e.target.checked)}
              className="accent-pink"
            />
            start after create
          </label>
        </div>

        <div className="hairline" />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-muted">
            {defaults.used_vmids.length} vmid
            {defaults.used_vmids.length === 1 ? "" : "s"} in use · next free{" "}
            <span className="metric text-fg">{defaults.next_vmid}</span>
          </div>
          <button type="submit" className="btn-pink" disabled={!formOk || busy || templates.length === 0}>
            {busy ? "creating…" : "create VM"}
          </button>
        </div>
      </form>
    </div>
  );
}
