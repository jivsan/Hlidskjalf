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
  lldpNeighbor?: { system_name?: string; port?: string } | null;
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

  // Top talkers by total rate
  const topTalkers = [...data]
    .sort((a, b) => (b.inputRate + b.outputRate) - (a.inputRate + a.outputRate))
    .slice(0, 5);

  // SVG faceplate mimicking physical Arista 7050TX-48T-4SFP+ (48x 10G-T + 4 SFP+)
  // More rack-like: bezel, port rows, LEDs, clickable for notes
  const renderSVG = () => (
    <svg width="100%" height="140" viewBox="0 0 640 140" className="svg-faceplate bg-[#0f1117] border border-[#2f3549] rounded" style={{ maxWidth: 640 }}>
      {/* Rack bezel / faceplate frame - Flux-like clean industrial */}
      <rect x="4" y="4" width="632" height="132" rx="3" fill="#1a1b26" stroke="#3a415a" strokeWidth="3" />
      <text x="320" y="18" textAnchor="middle" fontSize="9" fill="#565f89" fontFamily="monospace">ARISTA 7050TX-48T-4SFP+  •  FRONT PANEL</text>

      {/* 48x 10GBASE-T ports: 2 rows of 24 */}
      {Array.from({ length: 48 }).map((_, i) => {
        const name = `Ethernet${i + 1}`;
        const p = data.find((pp) => pp.name === name);
        const isUp = p?.status === "connected";
        const isActive = p?.active;
        const row = i < 24 ? 0 : 1;
        const col = i % 24;
        const x = 20 + col * 24;
        const y = 30 + row * 50;
        const color = isUp ? "#22c55e" : "#f7768e";
        return (
          <g key={name} onClick={() => { if (p) { setEditing(name); setNoteDraft(p.note); } }} style={{ cursor: 'pointer' }}>
            {/* Port rectangle */}
            <rect x={x} y={y} width="18" height="28" rx="2" fill={isUp ? "#0a2a1a" : "#2a2a2a"} stroke={color} strokeWidth="1.5" />
            {/* Port number */}
            <text x={x + 9} y={y + 38} textAnchor="middle" fontSize="6" fill="#c0caf5">{i + 1}</text>
            {/* Activity LED (blinks if active) */}
            {isActive && (
              <circle cx={x + 9} cy={y - 5} r="3" fill={row === 0 ? "#2de2e6" : "#ff4fa3"}>
                <animate attributeName="opacity" values="0.4;1;0.4" dur="0.8s" repeatCount="indefinite" />
              </circle>
            )}
            {/* LLDP indicator if present */}
            {p?.lldpNeighbor && <circle cx={x + 15} cy={y + 5} r="2" fill="#e0af68" />}
          </g>
        );
      })}

      {/* 4x SFP+ uplinks on right */}
      {[49, 50, 51, 52].map((n, idx) => {
        const name = `Ethernet${n}`;
        const p = data.find((pp) => pp.name === name);
        const isUp = p?.status === "connected";
        const x = 520 + idx * 28;
        const y = 35;
        return (
          <g key={name} onClick={() => { if (p) { setEditing(name); setNoteDraft(p.note); } }} style={{ cursor: 'pointer' }}>
            <rect x={x} y={y} width="20" height="12" rx="1" fill="#222" stroke={isUp ? "#22c55e" : "#f7768e"} strokeWidth="1.5" />
            <text x={x + 10} y={y + 26} textAnchor="middle" fontSize="5" fill="#c0caf5">S{n - 48}</text>
            {p?.active && <circle cx={x + 10} cy={y - 4} r="2.5" fill="#ff4fa3"><animate attributeName="opacity" values="0.5;1;0.5" dur="0.6s" repeatCount="indefinite" /></circle>}
          </g>
        );
      })}
    </svg>
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl tracking-wide">SWITCH <span className="text-cyan">7050TX</span></h1>
          <div className="text-xs text-muted metric">
            10.0.20.2 • {connected}/{data.length} ports live • physical faceplate view
          </div>
        </div>
        <div className="text-[10px] text-muted tracking-[2px] border border-border-token px-2 py-0.5 rounded">
          RACK 47 • TOKYO NIGHT
        </div>
      </div>

      {ports.error && (
        <div className="card border-red/40 p-4 text-red text-sm">
          could not reach switch at 10.0.20.2 — check eAPI creds and network
        </div>
      )}

      {/* SVG Physical Faceplate - rack like */}
      <div>
        <div className="text-xs uppercase tracking-wider text-muted mb-1">Physical Faceplate (click port to edit note)</div>
        {renderSVG()}
      </div>

      {/* Top Talkers - new */}
      {topTalkers.length > 0 && (
        <div className="card p-3">
          <div className="text-xs uppercase tracking-wider text-muted mb-2">Top Talkers (live rates)</div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm metric">
            {topTalkers.map((p, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="font-mono text-fg">{p.name}</span>
                <span className="text-muted">→</span>
                <span>{formatRate(p.inputRate + p.outputRate)}</span>
                {p.lldpNeighbor && <span className="text-[10px] text-amber">({p.lldpNeighbor.system_name})</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Port list - Flux-inspired cleaner cards, show LLDP + desc + note */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
        {data.map((port) => {
          const isActive = port.active;
          const isUp = port.status === "connected";
          const isEditing = editing === port.name;
          const lldp = port.lldpNeighbor;

          return (
            <div
              key={port.name}
              className={`card p-3 space-y-1.5 border ${isUp ? "border-cyan/20" : "border-border-token"} text-sm switch-port`}
            >
              <div className="flex items-center justify-between">
                <div className="font-mono text-sm text-fg tracking-wider">{port.name}</div>
                <div
                  className={`w-2 h-2 rounded-full ${isUp ? "bg-cyan" : "bg-red"} ${isActive ? "animate-pulse" : ""}`}
                  title={port.status}
                />
              </div>

              <div className="text-[10px] text-muted tracking-widest flex gap-2">
                <span>{port.speed || "—"}</span>
                {port.vlan && <span>VLAN {port.vlan}</span>}
              </div>

              {/* Blinking activity lights - subtle Flux style */}
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

              {/* LLDP neighbor */}
              {lldp && (
                <div className="text-[10px] text-amber">
                  LLDP → {lldp.system_name} ({lldp.port})
                </div>
              )}

              {/* Description from switch + note */}
              <div className="text-xs text-muted min-h-[1.5rem]">
                {port.description || <span className="italic">no description</span>}
              </div>

              {isEditing ? (
                <div className="space-y-1">
                  <input
                    className="input text-xs py-1"
                    value={noteDraft}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    placeholder="your note..."
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
        SVG = physical faceplate • click port for notes • LLDP shows connected machines • top talkers above
      </div>
    </div>
  );
}

function formatRate(bps: number): string {
  if (!bps) return "0";
  if (bps < 1_000_000) return (bps / 1000).toFixed(0) + "k";
  return (bps / 1_000_000).toFixed(1) + "M";
}
