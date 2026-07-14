import { useEffect, useState, type FormEvent } from "react";
import {
  getPveConnection,
  putPveConnection,
  testPveConnection,
  type PveConnection,
  type PveConnectionUpdate,
  type PveProbeResult,
} from "../../api";
import { useToast } from "../../components/Toast";
import { ErrorState, LoadingState } from "../../components/ui";

/** Editing the Proxmox connection after setup has closed.
 *
 *  Nothing here is saved unless Proxmox answers to it — a saved-but-broken
 *  connection leaves the panel unable to reach Proxmox with no way back except
 *  the database. That is also why there is no "reset to wizard": the setup
 *  endpoints are unauthenticated by construction, and reopening them would be a
 *  window for anyone on the network to make themselves an admin.
 */
export function ProxmoxTab() {
  const toast = useToast();
  const [current, setCurrent] = useState<PveConnection | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [form, setForm] = useState<PveConnectionUpdate | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [probe, setProbe] = useState<PveProbeResult | null>(null);

  useEffect(() => {
    getPveConnection()
      .then((c) => {
        setCurrent(c);
        setForm({
          host: c.host,
          port: c.port,
          node: c.node,
          scheme: c.scheme,
          token_id: c.token_id,
          token_secret: "", // blank = keep the stored one
          fingerprint: c.fingerprint,
          tls: c.tls,
        });
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (loadError) return <ErrorState message={loadError} />;
  if (!current || !form) return <LoadingState />;

  const locked = current.env_locked.length > 0;
  const set = <K extends keyof PveConnectionUpdate>(k: K, v: PveConnectionUpdate[K]) => {
    setForm({ ...form, [k]: v });
    setProbe(null);
  };

  const run = async (what: "test" | "save", e?: FormEvent) => {
    e?.preventDefault();
    setBusy(what);
    try {
      if (what === "test") {
        const r = await testPveConnection(form);
        setProbe(r);
        toast.success(`connected — node ${r.node}, ${r.guests} guests`);
      } else {
        const saved = await putPveConnection(form);
        setCurrent(saved);
        setProbe(saved.tested);
        setForm({ ...form, token_secret: "" });
        toast.success("Proxmox connection saved");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "connection failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <form onSubmit={(e) => run("save", e)} className="card p-5 space-y-6">
      {locked && (
        <div className="rounded border border-amber/40 bg-amber/5 p-3 text-xs text-amber">
          The environment sets {current.env_locked.join(", ")} — and the environment always
          wins, so a change here would silently revert on the next restart. Unset those
          variables (on NixOS: leave the option null) to manage the connection here.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <div className="md:col-span-2">
          <label className="label" htmlFor="pve-host">host</label>
          <input
            id="pve-host"
            className="input metric"
            value={form.host}
            onChange={(e) => set("host", e.target.value.trim())}
            placeholder="proxmox.example.org"
            spellCheck={false}
            required
          />
        </div>
        <div>
          <label className="label" htmlFor="pve-port">port</label>
          <input
            id="pve-port"
            type="number"
            className="input metric"
            value={form.port}
            onChange={(e) => set("port", Number(e.target.value))}
            required
          />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="label" htmlFor="pve-node">node name</label>
          <input
            id="pve-node"
            className="input metric"
            value={form.node}
            onChange={(e) => set("node", e.target.value.trim())}
            placeholder="pve"
            spellCheck={false}
            required
          />
          <p className="text-xs text-muted mt-1">
            exactly what Proxmox calls it — the name in its own web UI tree. Get this
            wrong and every node-scoped page fails with a DNS error, because Proxmox
            tries to proxy the request to a host by that name.
          </p>
        </div>
        <div>
          <label className="label" htmlFor="pve-token">token id</label>
          <input
            id="pve-token"
            className="input metric"
            value={form.token_id}
            onChange={(e) => set("token_id", e.target.value.trim())}
            placeholder="hlidskjalf@pve!panel"
            spellCheck={false}
            required
          />
        </div>
      </div>

      <div>
        <label className="label" htmlFor="pve-secret">token secret</label>
        <input
          id="pve-secret"
          type="password"
          className="input metric"
          value={form.token_secret}
          onChange={(e) => set("token_secret", e.target.value)}
          placeholder={current.token_secret_set ? "•••••••• (unchanged)" : "paste the secret"}
          spellCheck={false}
          autoComplete="off"
        />
        <p className="text-xs text-muted mt-1">
          {current.token_secret_set
            ? "a secret is stored (encrypted at rest). Leave blank to keep it — paste a new one only when rotating."
            : "no secret stored yet."}
        </p>
      </div>

      <div className="space-y-3">
        <div className="eyebrow">certificate</div>
        <div className="space-y-2">
          {(
            [
              [
                "pin",
                "pin this exact certificate",
                "Right for the self-signed certificate Proxmox ships: no CA can vouch for it, so the panel accepts one certificate and nothing else.",
              ],
              [
                "system",
                "verify like a browser (CA + hostname)",
                "Right when Proxmox serves a real certificate (Let's Encrypt / ACME). A pin would break on every renewal; a signature survives it. Needs the hostname, not an IP.",
              ],
            ] as const
          ).map(([value, title, why]) => (
            <label
              key={value}
              className={`flex gap-3 items-start p-3 rounded border cursor-pointer ${
                form.tls === value ? "border-cyan/60 bg-cyan/5" : "border-border-token"
              }`}
            >
              <input
                type="radio"
                name="tls"
                className="accent-cyan mt-1"
                checked={form.tls === value}
                onChange={() => set("tls", value)}
              />
              <span>
                <span className="text-sm">{title}</span>
                <span className="block text-xs text-muted mt-0.5">{why}</span>
              </span>
            </label>
          ))}
        </div>

        {form.tls === "pin" && (
          <div>
            <label className="label" htmlFor="pve-fp">SHA-256 fingerprint</label>
            <input
              id="pve-fp"
              className="input metric text-xs"
              value={form.fingerprint}
              onChange={(e) => set("fingerprint", e.target.value.trim())}
              placeholder="AA:BB:…:FF"
              spellCheck={false}
            />
            <p className="text-xs text-muted mt-1">
              read it from the certificate Proxmox actually serves:{" "}
              <span className="metric">
                openssl s_client -connect {form.host || "host"}:{form.port} &lt;/dev/null | openssl
                x509 -noout -fingerprint -sha256
              </span>
            </p>
          </div>
        )}
      </div>

      {probe?.ok && (
        <div className="rounded border border-cyan/40 bg-cyan/5 p-3 text-xs text-cyan">
          connected — node <span className="metric">{probe.node}</span>, {probe.guests} guests.
          {probe.nodes.length > 1 && ` this Proxmox has: ${probe.nodes.join(", ")}`}
        </div>
      )}

      <div className="hairline" />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted">
          nothing is saved unless Proxmox answers to it.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn-plain"
            disabled={busy !== null || locked}
            onClick={() => run("test")}
          >
            {busy === "test" ? "testing…" : "test connection"}
          </button>
          <button type="submit" className="btn-pink" disabled={busy !== null || locked}>
            {busy === "save" ? "saving…" : "save"}
          </button>
        </div>
      </div>
    </form>
  );
}
