import { useState } from "react";
import { api } from "../api";
import { useToast } from "../components/Toast";
import { LoadingState } from "../components/ui";
import { usePoll } from "../hooks/usePoll";

interface Port {
  name: string;
  status: string;
  speed: string;
  duplex: string;
  vlan: string | null;
  description: string;
  note: string;
  inputRate: number;
  outputRate: number;
  active: boolean;
}

export function SwitchPage() {
  const toast = useToast();
  const [editing, setEditing] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");

  const ports = usePoll(() => api.get<Port[]>("/api/switch/ports"), 4000);

  const saveNote = async (name: string) => {
    try {
      await api.post(`/api/switch/ports/${encodeURIComponent(name)}/note`, {
        note: noteDraft,
      });
      toast.success(`note saved for ${name}`);
      setEditing(null);
      ports.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "failed to save note");
    }
  };

  if (ports.loading) return <LoadingState message="connecting to switch…" />;

  const data = ports.data ?? [];
  const connected = data.filter((p) => p.status === "connected").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl tracking-wide">SWITCH <span className="text-cyan">7050TX</span></h1>
          <div className="text-xs text-muted metric">
            10.0.20.2 • {connected}/{data.length} ports live • cyan/pink rack
          </div>
        </div>
        <div className="text-[10px] text-muted tracking-[2px] border border-border-token px-2 py-0.5 rounded">
          RACK 47 • TOKYO NIGHT
        </div>
      </div>

      {ports.error && (
        <div className="card border-red/40 p-4 text-red text-sm">
          could not reach switch at 10.0.20.2 — check SSH/eAPI creds and network
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
        {data.map((port) => {
          const isActive = port.active;
          const isUp = port.status === "connected";
          const isEditing = editing === port.name;

          return (
            <div
              key={port.name}
              className={`card p-3 space-y-2 border ${isUp ? "border-cyan/30" : "border-border-token"}`}
            >
              <div className="flex items-center justify-between">
                <div className="font-mono text-sm text-fg tracking-wider">{port.name}</div>
                <div
                  className={`w-2 h-2 rounded-full ${isUp ? "bg-cyan" : "bg-red"} ${isActive ? "glow-cyan animate-pulse" : ""}`}
                  title={port.status}
                />
              </div>

              <div className="text-[10px] text-muted tracking-widest flex gap-2">
                <span>{port.speed || "—"}</span>
                {port.vlan && <span>VLAN {port.vlan}</span>}
              </div>

              {/* Blinking activity lights */}
              <div className="flex gap-3 text-[10px]">
                <div className="flex items-center gap-1.5">
                  <span className={`led led-cyan ${isActive ? "led-active" : "led-muted"}`} />
                  <span className="text-muted">IN</span>
                  <span className="tabular-nums text-xs">{formatRate(port.inputRate)}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className={`led led-pink ${isActive ? "led-active" : "led-muted"}`} />
                  <span className="text-muted">OUT</span>
                  <span className="tabular-nums text-xs">{formatRate(port.outputRate)}</span>
                </div>
              </div>

              {/* Description + note */}
              <div className="text-xs text-muted min-h-[2.25rem]">
                {port.description || <span className="italic">no description on switch</span>}
              </div>

              {isEditing ? (
                <div className="space-y-1">
                  <input
                    className="input text-xs py-1"
                    value={noteDraft}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    placeholder="your note for this port..."
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveNote(port.name);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    autoFocus
                  />
                  <div className="flex gap-2">
                    <button className="btn-plain text-[10px] px-2 py-0.5" onClick={() => setEditing(null)}>
                      cancel
                    </button>
                    <button className="btn-cyan text-[10px] px-2 py-0.5" onClick={() => void saveNote(port.name)}>
                      save
                    </button>
                  </div>
                </div>
              ) : (
                <div
                  className="group flex justify-between items-center text-xs cursor-pointer"
                  onClick={() => {
                    setEditing(port.name);
                    setNoteDraft(port.note);
                  }}
                >
                  <span className={port.note ? "text-fg" : "text-muted italic group-hover:text-pink"}>
                    {port.note || "click to add note"}
                  </span>
                  <span className="text-pink opacity-0 group-hover:opacity-100 text-[10px]">✎</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="text-[10px] text-muted tracking-widest">
        ports blink when traffic &gt; ~1 kbps • notes are stored in the panel (override switch description)
      </div>
    </div>
  );
}

function formatRate(bps: number): string {
  if (!bps) return "0";
  if (bps < 1_000_000) return (bps / 1000).toFixed(0) + "k";
  return (bps / 1_000_000).toFixed(1) + "M";
}
