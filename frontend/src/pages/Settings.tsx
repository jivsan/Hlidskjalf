import { useEffect, useState, type FormEvent } from "react";
import {
  getProvisionSettings,
  putProvisionSettings,
  type ProvisionSettings,
} from "../api";
import { ErrorState, LoadingState, PageHeader } from "../components/ui";

const IPV4_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

interface VlanRow {
  tag: string;
  gateway: string;
}

function rowsFrom(gateways: Record<string, string>): VlanRow[] {
  return Object.entries(gateways)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([tag, gateway]) => ({ tag, gateway }));
}

function tagOk(tag: string): boolean {
  return /^\d+$/.test(tag) && Number(tag) >= 1 && Number(tag) <= 4094;
}

function gatewayOk(gw: string): boolean {
  return gw === "" || IPV4_RE.test(gw);
}

/** A select fed from live node options; keeps a stale current value selectable. */
function OptionSelect({
  id,
  value,
  options,
  locked,
  onChange,
}: {
  id: string;
  value: string;
  options: string[];
  locked: boolean;
  onChange: (v: string) => void;
}) {
  const all = options.includes(value) || !value ? options : [value, ...options];
  return (
    <select
      id={id}
      className="input metric"
      value={value}
      disabled={locked}
      onChange={(e) => onChange(e.target.value)}
    >
      {all.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function LockedNote() {
  return <p className="text-xs text-muted mt-1">set by environment — locked</p>;
}

export function SettingsPage() {
  const [loaded, setLoaded] = useState<ProvisionSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [rows, setRows] = useState<VlanRow[]>([]);
  const [storage, setStorage] = useState("");
  const [bridge, setBridge] = useState("");
  const [newTag, setNewTag] = useState("");
  const [newGateway, setNewGateway] = useState("");

  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const adopt = (s: ProvisionSettings) => {
    setLoaded(s);
    setRows(rowsFrom(s.vlan_gateways));
    setStorage(s.clone_storage);
    setBridge(s.bridge);
  };

  useEffect(() => {
    getProvisionSettings()
      .then(adopt)
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (loadError) return <ErrorState message={loadError} />;
  if (!loaded) return <LoadingState />;

  const locked = (key: string) => loaded.env_locked.includes(key);
  const vlansLocked = locked("vlan_gateways");

  const addRow = () => {
    if (!tagOk(newTag) || !gatewayOk(newGateway)) return;
    if (rows.some((r) => r.tag === newTag)) return;
    setRows(
      [...rows, { tag: newTag, gateway: newGateway }].sort(
        (a, b) => Number(a.tag) - Number(b.tag),
      ),
    );
    setNewTag("");
    setNewGateway("");
  };

  const removeRow = (tag: string) => setRows(rows.filter((r) => r.tag !== tag));

  const setRowGateway = (tag: string, gateway: string) =>
    setRows(rows.map((r) => (r.tag === tag ? { ...r, gateway } : r)));

  const rowsOk = rows.every((r) => tagOk(r.tag) && gatewayOk(r.gateway));
  const addPending = newTag !== "" || newGateway !== "";
  const formOk = rowsOk && storage.trim() !== "" && bridge.trim() !== "";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!formOk || busy) return;
    setBusy(true);
    setSaveError(null);
    setSavedAt(null);
    try {
      const vlan_gateways: Record<string, string> = {};
      for (const r of rows) vlan_gateways[r.tag] = r.gateway;
      const res = await putProvisionSettings({
        vlan_gateways,
        clone_storage: storage,
        bridge,
      });
      adopt(res);
      setSavedAt(Date.now());
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader eyebrow="panel configuration" title="Settings" />

      {loaded.warning && (
        <div className="card border-amber/40 p-3 text-amber text-xs">
          could not query the node for live options — values are accepted
          unchecked ({loaded.warning})
        </div>
      )}

      <form onSubmit={submit} className="card p-5 space-y-6">
        <div className="eyebrow">provisioning</div>

        {/* VLANs */}
        <div className="space-y-3">
          <div>
            <span className="label">VLANs offered on the provision form</span>
            {vlansLocked && <LockedNote />}
          </div>
          {rows.length === 0 && !vlansLocked && (
            <p className="text-xs text-muted">
              no VLANs yet — without at least one, no VM can be provisioned
            </p>
          )}
          <div className="space-y-2">
            {rows.map((r) => (
              <div key={r.tag} className="flex items-center gap-2">
                <span className="input metric w-24 text-center opacity-80" aria-label={`VLAN tag ${r.tag}`}>
                  {r.tag}
                </span>
                <input
                  className="input metric flex-1"
                  value={r.gateway}
                  disabled={vlansLocked}
                  onChange={(e) => setRowGateway(r.tag, e.target.value.trim())}
                  placeholder="gateway (may be empty)"
                  spellCheck={false}
                  aria-label={`gateway for VLAN ${r.tag}`}
                />
                {!vlansLocked && (
                  <button
                    type="button"
                    className="btn-plain px-2"
                    onClick={() => removeRow(r.tag)}
                    aria-label={`remove VLAN ${r.tag}`}
                  >
                    remove
                  </button>
                )}
                {r.gateway && !gatewayOk(r.gateway) && (
                  <span className="text-red text-xs">bad IPv4</span>
                )}
              </div>
            ))}
          </div>
          {!vlansLocked && (
            <div className="flex items-center gap-2">
              <input
                className="input metric w-24"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value.trim())}
                placeholder="tag"
                inputMode="numeric"
                spellCheck={false}
                aria-label="new VLAN tag"
              />
              <input
                className="input metric flex-1"
                value={newGateway}
                onChange={(e) => setNewGateway(e.target.value.trim())}
                placeholder="gateway (may be empty)"
                spellCheck={false}
                aria-label="new VLAN gateway"
              />
              <button
                type="button"
                className="btn-plain px-2"
                onClick={addRow}
                disabled={
                  !tagOk(newTag) ||
                  !gatewayOk(newGateway) ||
                  rows.some((r) => r.tag === newTag)
                }
              >
                add
              </button>
            </div>
          )}
          {addPending && newTag !== "" && !tagOk(newTag) && (
            <p className="text-red text-xs">tag must be an integer 1–4094</p>
          )}
        </div>

        {/* storage + bridge */}
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="s-storage">
              clone storage
            </label>
            <OptionSelect
              id="s-storage"
              value={storage}
              options={loaded.options.storages}
              locked={locked("clone_storage")}
              onChange={setStorage}
            />
            {locked("clone_storage") && <LockedNote />}
          </div>
          <div>
            <label className="label" htmlFor="s-bridge">
              bridge
            </label>
            <OptionSelect
              id="s-bridge"
              value={bridge}
              options={loaded.options.bridges}
              locked={locked("bridge")}
              onChange={setBridge}
            />
            {locked("bridge") && <LockedNote />}
          </div>
        </div>

        <div className="hairline" />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs min-w-0">
            {saveError && <span className="text-red">{saveError}</span>}
            {!saveError && savedAt != null && (
              <span className="text-cyan">saved — provisioning uses these now</span>
            )}
            {!saveError && savedAt == null && addPending && (
              <span className="text-muted">unadded VLAN row — press add first</span>
            )}
          </div>
          <button type="submit" className="btn-cyan" disabled={!formOk || busy}>
            {busy ? "saving…" : "save"}
          </button>
        </div>
      </form>
    </div>
  );
}
