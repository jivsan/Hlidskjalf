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
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");

  const portsPoll = usePoll(() => api.get<Port[]>("/api/switch/ports"), 4000);

  const saveNote = async (name: string) => {
    try {
      await api.post(`/api/switch/ports/${encodeURIComponent(name)}/note`, {
        note: noteDraft,
      });
      toast.success(`note saved for ${name}`);
      setEditing(null);
      portsPoll.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "failed to save note");
    }
  };

  if (portsPoll.loading) return <LoadingState message="connecting to switch…" />;

  const data = portsPoll.data ?? [];
  const connected = data.filter((p) => p.status === "connected").length;
  const portMap = new Map(data.map((p) => [p.name, p] as const));

  const topTalkers = [...data]
    .sort((a, b) => (b.inputRate + b.outputRate) - (a.inputRate + a.outputRate))
    .slice(0, 5);

  const selectedPort = selected ? portMap.get(selected) : null;

  const selectPort = (name: string) => {
    setSelected(name);
  };

  // SVG-based faceplate mimicking physical Arista 7050TX-48T-4SFP+ (48 10G-T in 2 rows of 24 + 4 SFP+)
  const renderFaceplate = () => {
    const viewW = 700;
    const viewH = 158;
    const pW = 17;
    const pH = 11;
    const gap = 2.8;
    const startX = 28;
    const topRowY = 54;
    const botRowY = 98;
    const sfpX = 600;

    const copper: Array<{ name: string; num: number; row: 0 | 1; col: number }> = [];
    for (let i = 1; i <= 48; i++) {
      const name = `Ethernet${i}`;
      const row: 0 | 1 = i <= 24 ? 0 : 1;
      const col = i <= 24 ? i - 1 : i - 25;
      copper.push({ name, num: i, row, col });
    }
    const sfps = [49, 50, 51, 52].map((n) => ({ name: `Ethernet${n}`, num: n }));

    return (
      <svg
        viewBox={`0 0 ${viewW} ${viewH}`}
        className="w-full select-none"
        style={{ maxHeight: 205 }}
        role="img"
        aria-label="Arista 7050TX-48 front panel faceplate"
      >
        {/* main faceplate panel */}
        <rect x="1" y="1" width={viewW - 2} height={viewH - 2} rx="2" fill="#0b0d15" stroke="#2b3144" strokeWidth="1.5" />

        {/* subtle panel texture */}
        <g stroke="#151a26" strokeWidth="0.6" opacity="0.6">
          {Array.from({ length: 5 }).map((_, i) => (
            <line key={i} x1="18" y1={22 + i * 3} x2={viewW - 18} y2={22 + i * 3} />
          ))}
        </g>

        {/* header labels */}
        <text x="10" y="15" fill="#5a617d" fontSize="7.5" fontFamily="inherit" letterSpacing="0.5">ARISTA</text>
        <text x="10" y="27" fill="#a5b0d4" fontSize="9" fontFamily="inherit">7050TX-48T-4SFP+</text>

        {/* row labels */}
        <text x="7" y={topRowY + 7} fill="#4a516a" fontSize="5.5">1–24</text>
        <text x="7" y={botRowY + 7} fill="#4a516a" fontSize="5.5">25–48</text>
        <text x={sfpX - 3} y="34" fill="#4a516a" fontSize="5.5">SFP+</text>

        {/* 48× 10G-T RJ45 ports */}
        {copper.map(({ name, num, row, col }) => {
          const x = startX + col * (pW + gap);
          const y = row === 0 ? topRowY : botRowY;
          const pd = portMap.get(name);
          const isUp = pd?.status === "connected";
          const isActive = !!pd?.active;
          const isSel = selected === name;
          const ledFill = isUp ? "#22c55e" : "#f7768e";

          return (
            <g
              key={name}
              className={`svg-port ${isSel ? "selected" : ""}`}
              onClick={() => selectPort(name)}
            >
              {/* RJ45 body */}
              <rect
                x={x}
                y={y}
                width={pW}
                height={pH}
                rx="1.8"
                ry="1.8"
                fill="#0d101b"
                stroke={isSel ? "#ff4fa3" : "#2f3548"}
                strokeWidth={isSel ? 1.4 : 0.75}
              />
              {/* jack recess */}
              <rect x={x + 2.2} y={y + 2.8} width={pW - 4.4} height={pH - 5.2} rx="0.6" fill="#161b28" />
              {/* status LED (green for up, red for down; blinks for activity) */}
              <circle
                cx={x + pW / 2}
                cy={y - 3.8}
                r="2.65"
                fill={ledFill}
                className={`svg-led ${isActive ? "led-active" : ""}`}
              />
              {/* port number label */}
              <text x={x + pW / 2} y={y + pH + 8.5} textAnchor="middle" className="svg-port-label">
                {num}
              </text>
            </g>
          );
        })}

        {/* 4× SFP+ ports (right side) */}
        {sfps.map(({ name, num }, idx) => {
          const x = sfpX;
          const y = 40 + idx * 23.5;
          const pd = portMap.get(name);
          const isUp = pd?.status === "connected";
          const isActive = !!pd?.active;
          const isSel = selected === name;
          const ledFill = isUp ? "#22c55e" : "#f7768e";

          return (
            <g
              key={name}
              className={`svg-port ${isSel ? "selected" : ""}`}
              onClick={() => selectPort(name)}
            >
              {/* SFP+ cage */}
              <rect
                x={x}
                y={y}
                width="9.5"
                height="17.5"
                rx="1.2"
                fill="#0d101b"
                stroke={isSel ? "#ff4fa3" : "#2f3548"}
                strokeWidth={isSel ? 1.3 : 0.7}
              />
              {/* slot opening */}
              <rect x={x + 1.6} y={y + 2.2} width="6.3" height="13" rx="0.4" fill="#171c2a" />
              {/* SFP LED */}
              <circle
                cx={x + 4.75}
                cy={y - 3.2}
                r="2.25"
                fill={ledFill}
                className={`svg-led ${isActive ? "led-active" : ""}`}
              />
              <text x={x + 4.75} y={y + 22.5} textAnchor="middle" className="svg-port-label">
                {num}
              </text>
            </g>
          );
        })}

        {/* footer label */}
        <text x={viewW - 12} y={viewH - 7} fill="#353b4f" fontSize="5.5" textAnchor="end">RACK 47</text>
      </svg>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl tracking-wide">
            SWITCH <span className="text-cyan">7050TX-48T</span>
          </h1>
          <div className="text-xs text-muted metric">
            10.0.20.2 • {connected} up • {data.length} reported • SVG physical faceplate
          </div>
        </div>
        <div className="text-[10px] text-muted tracking-[2px] border border-border-token px-2 py-0.5 rounded">
          RACK 47 • TOKYO NIGHT
        </div>
      </div>

      {portsPoll.error && (
        <div className="card border-red/40 p-4 text-red text-sm">
          could not reach switch at 10.0.20.2 — check eAPI creds (management api http-commands) and network
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-6">
        {/* SVG Faceplate + rack bezel */}
        <div className="flex-1 min-w-0">
          <div className="relative mx-[22px]">
            <div className="rack-ear rack-ear-left" />
            <div className="rack-ear rack-ear-right" />
            <div className="rack-bezel">{renderFaceplate()}</div>
          </div>
          <div className="mt-1.5 px-1 flex items-center justify-between text-[10px] text-muted tracking-widest">
            <span>click ports • green=up / red=down • blink=activity (&gt;1 kbps)</span>
            <span>48×10G-T RJ45 + 4×SFP+</span>
          </div>
        </div>

        {/* Sidebar: Top Talkers + Enhanced Port Details */}
        <div className="w-full lg:w-80 flex-shrink-0 space-y-4">
          {/* Top Talkers */}
          <div className="card p-3">
            <div className="flex items-baseline justify-between mb-1.5">
              <div className="text-xs uppercase tracking-[1px] text-muted">TOP TALKERS</div>
              <div className="text-[10px] text-muted">by total rate</div>
            </div>
            {topTalkers.length === 0 ? (
              <div className="py-3 text-center text-xs text-muted italic">no live rate data</div>
            ) : (
              <div className="space-y-[3px]">
                {topTalkers.map((p, i) => {
                  const total = (p.inputRate || 0) + (p.outputRate || 0);
                  const isSel = selected === p.name;
                  const isUp = p.status === "connected";
                  return (
                    <div
                      key={p.name}
                      onClick={() => selectPort(p.name)}
                      className={`flex items-center justify-between rounded px-2 py-1 text-xs cursor-pointer transition-colors hover:bg-border-token/40 ${isSel ? "bg-border-token/50" : ""}`}
                    >
                      <div className="flex items-center gap-1.5 font-mono">
                        <span className="w-3 text-right text-muted tabular-nums">{i + 1}</span>
                        <span>{p.name.replace("Ethernet", "Et")}</span>
                        <span className={`inline-block w-1.5 h-1.5 rounded-full ${isUp ? "bg-[#22c55e]" : "bg-red"}`} />
                      </div>
                      <span className="tabular-nums text-muted text-[10px]">{formatRate(total)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Port Details (with desc + LLDP + inline editable notes) */}
          <div className="card p-3 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-xs uppercase tracking-[1px] text-muted">PORT DETAILS</div>
              {selected && (
                <button
                  className="btn-plain text-[10px] px-1.5 py-px"
                  onClick={() => {
                    setSelected(null);
                    setEditing(null);
                  }}
                >
                  × close
                </button>
              )}
            </div>

            {!selectedPort ? (
              <div className="py-6 text-center text-xs text-muted italic">
                select a port from the faceplate or top talkers list
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <div className="font-mono text-[15px] tracking-wider text-fg">{selectedPort.name}</div>
                  <div
                    className={`w-2.5 h-2.5 rounded-full ${selectedPort.status === "connected" ? "bg-[#22c55e]" : "bg-red"} ${selectedPort.active ? "led-active" : ""}`}
                    title={selectedPort.status}
                  />
                </div>

                <div className="grid grid-cols-2 gap-x-4 gap-y-[1px] text-[10px]">
                  <div className="text-muted">speed <span className="text-fg tabular-nums">{selectedPort.speed || "—"}</span></div>
                  <div className="text-muted">duplex <span className="text-fg">{selectedPort.duplex || "—"}</span></div>
                  <div className="text-muted">vlan <span className="text-fg">{selectedPort.vlan || "—"}</span></div>
                  <div className="text-muted">state <span className="text-fg">{selectedPort.status}</span></div>
                </div>

                {/* live rates + integrated LEDs (using shared styles) */}
                <div className="flex gap-4 pt-0.5 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className={`led led-cyan ${selectedPort.active ? "led-active" : "led-muted"}`} />
                    <span className="text-muted">IN</span>
                    <span className="tabular-nums text-fg">{formatRate(selectedPort.inputRate)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className={`led led-pink ${selectedPort.active ? "led-active" : "led-muted"}`} />
                    <span className="text-muted">OUT</span>
                    <span className="tabular-nums text-fg">{formatRate(selectedPort.outputRate)}</span>
                  </div>
                </div>

                {/* desc / LLDP / notes integrated */}
                <div className="pt-2 space-y-2 border-t border-border-token text-xs">
                  <div>
                    <div className="uppercase tracking-wider text-[10px] text-muted mb-px">SWITCH DESCRIPTION</div>
                    <div className="text-fg leading-snug">{selectedPort.description || <span className="italic text-muted">no description configured on switch</span>}</div>
                  </div>

                  {selectedPort.lldpNeighbor && (
                    <div>
                      <div className="uppercase tracking-wider text-[10px] text-muted mb-px">LLDP NEIGHBOR</div>
                      <div className="text-fg">
                        {selectedPort.lldpNeighbor.system_name || "—"}
                        {selectedPort.lldpNeighbor.port ? <span className="text-muted"> · {selectedPort.lldpNeighbor.port}</span> : null}
                      </div>
                    </div>
                  )}

                  {/* editable notes — inline, integrated */}
                  <div>
                    <div className="uppercase tracking-wider text-[10px] text-muted mb-px">
                      NOTES <span className="normal-case text-pink/60">(local to panel)</span>
                    </div>
                    {editing === selectedPort.name ? (
                      <div className="space-y-1">
                        <input
                          className="input text-xs py-1"
                          value={noteDraft}
                          onChange={(e) => setNoteDraft(e.target.value)}
                          placeholder="your note…"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void saveNote(selectedPort.name);
                            if (e.key === "Escape") setEditing(null);
                          }}
                          autoFocus
                        />
                        <div className="flex gap-1.5">
                          <button className="btn-plain text-[10px] px-2 py-px" onClick={() => setEditing(null)}>
                            cancel
                          </button>
                          <button className="btn-cyan text-[10px] px-2 py-px" onClick={() => void saveNote(selectedPort.name)}>
                            save
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div
                        className="group flex justify-between items-start gap-2 cursor-pointer py-0.5 rounded hover:bg-border-token/20 -mx-1 px-1"
                        onClick={() => {
                          setEditing(selectedPort.name);
                          setNoteDraft(selectedPort.note || "");
                        }}
                      >
                        <span className={selectedPort.note ? "text-fg" : "text-muted italic group-hover:text-pink"}>
                          {selectedPort.note || "click to add note"}
                        </span>
                        <span className="text-pink opacity-0 group-hover:opacity-100 text-[10px] mt-px">✎</span>
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="text-[10px] text-muted tracking-widest px-1">
        faceplate emulates physical Arista layout • ports clickable SVG elements • notes override in panel DB
      </div>
    </div>
  );
}

function formatRate(bps: number): string {
  if (!bps) return "0";
  if (bps < 1_000_000) return (bps / 1000).toFixed(0) + "k";
  return (bps / 1_000_000).toFixed(1) + "M";
}
