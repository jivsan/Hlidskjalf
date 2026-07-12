import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { api } from "../api";
import { useToast } from "../components/Toast";
import { ErrorState, LoadingState } from "../components/ui";
import { usePoll } from "../hooks/usePoll";
import type { SwitchPort, SwitchPortsResponse } from "../types";

type Port = SwitchPort;

export function SwitchPage() {
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");

  const portsPoll = usePoll(
    () => api.get<SwitchPortsResponse>("/api/switch/ports"),
    4000
  );

  const saveTimerRef = useRef<number | null>(null);
  const scheduleDebouncedSave = useCallback((name: string, note: string) => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(async () => {
      try {
        await api.post(`/api/switch/ports/${encodeURIComponent(name)}/note`, { note: note.trim() });
        portsPoll.refresh();
      } catch {}
    }, 650);
  }, [portsPoll]);

  const saveNote = async (name: string) => {
    if (saveTimerRef.current) { window.clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    const noteVal = noteDraft.trim();
    try {
      await api.post(`/api/switch/ports/${encodeURIComponent(name)}/note`, { note: noteVal });
      toast.success(`note saved for ${name}`);
      setEditing(null);
      portsPoll.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "failed to save note");
    }
  };

  useEffect(() => () => { if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current); }, []);

  const response = portsPoll.data as SwitchPortsResponse | Port[] | null;
  const data: Port[] = Array.isArray(response) ? response : (response && "ports" in response ? response.ports : []);
  const serverError: string | null = !Array.isArray(response) && response && "error" in response ? (response.error as string) || null : portsPoll.error;
  const hasData = data.length > 0;

  if (portsPoll.loading && !hasData) {
    return <LoadingState message="connecting to switch…" />;
  }

  const connected = data.filter((p) => p.status === "connected").length;
  const portMap = new Map(data.map((p) => [p.name, p] as const));

  const topTalkers = [...data]
    .sort((a, b) => (b.inputRate + b.outputRate) - (a.inputRate + b.outputRate))
    .slice(0, 5);

  const selectedPort = selected ? portMap.get(selected) : null;

  // Canvas refs and consts (top level hooks before useCallback)
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  const LOGICAL_W = 720;
  const LOGICAL_H = 175;





  // helpers

  const drawRJ45 = (ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, isUp: boolean, isActive: boolean, isHighlight: boolean, num: number, time: number) => {
    const body = isUp ? '#1a2232' : '#0e121e';
    ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.65)'; ctx.shadowBlur = 2.5; ctx.shadowOffsetX = 0.3; ctx.shadowOffsetY = 0.8;
    ctx.fillStyle = body; roundRect(ctx, x, y, w, h, 1.4); ctx.fill(); ctx.restore();
    ctx.fillStyle = 'rgba(255,255,255,0.09)'; roundRect(ctx, x + 0.4, y + 0.4, w - 0.8, 1.8, 0.9); ctx.fill();
    ctx.fillStyle = '#07090f'; roundRect(ctx, x + 1.6, y + 2.2, w - 3.2, h - 4, 0.7); ctx.fill();
    ctx.fillStyle = '#0c0f17'; ctx.fillRect(x + 3.2, y + 1.1, w - 6.4, 1.1);
    ctx.strokeStyle = '#252c3b'; ctx.lineWidth = 0.35;
    for (let k = 0; k < 8; k++) { const lx = x + 2.6 + (k * (w - 5.2) / 7.5); ctx.beginPath(); ctx.moveTo(lx, y + 3.6); ctx.lineTo(lx, y + h - 2); ctx.stroke(); }
    if (isHighlight) { ctx.strokeStyle = '#ff4fa3'; ctx.lineWidth = 1.1; roundRect(ctx, x - 1, y - 1, w + 2, h + 2, 1.8); ctx.stroke(); }
    const ledCx = x + w / 2, ledCy = y - 4.2; let ledCol = isUp ? '#22c55e' : '#f7768e'; ctx.save();
    let blinkBoost = 0; if (isActive) { const phase = Math.sin(time * 6.2) * 0.5 + 0.5; if (phase > 0.5) { ledCol = '#4ade80'; blinkBoost = 1; } }
    ctx.fillStyle = ledCol; ctx.beginPath(); ctx.arc(ledCx, ledCy, 1.95, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.55)'; ctx.lineWidth = 0.5; ctx.beginPath(); ctx.arc(ledCx, ledCy, 1.95, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,0.75)'; ctx.beginPath(); ctx.arc(ledCx - 0.65, ledCy - 0.65, 0.65, 0, Math.PI * 2); ctx.fill();
    if (blinkBoost) { ctx.fillStyle = 'rgba(74, 222, 128, 0.35)'; ctx.beginPath(); ctx.arc(ledCx, ledCy, 3.2, 0, Math.PI * 2); ctx.fill(); }
    ctx.restore();
    ctx.fillStyle = isHighlight ? '#c8d0e8' : '#5f677f'; ctx.font = '5px system-ui, monospace'; ctx.textAlign = 'center';
    ctx.fillText(String(num), x + w / 2, y + h + 7.2); ctx.textAlign = 'start';
  };
  const drawQSFP = (ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, isUp: boolean, isActive: boolean, isHighlight: boolean, num: number, time: number) => {
    ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 2; ctx.shadowOffsetY = 0.6;
    const cageGrad = ctx.createLinearGradient(x, y, x + w, y); cageGrad.addColorStop(0, '#181d29'); cageGrad.addColorStop(0.5, '#12161f'); cageGrad.addColorStop(1, '#0d1019');
    ctx.fillStyle = cageGrad; roundRect(ctx, x, y, w, h, 1.2); ctx.fill(); ctx.restore();
    ctx.strokeStyle = isHighlight ? '#ff4fa3' : '#2c3344'; ctx.lineWidth = isHighlight ? 1.3 : 0.7; roundRect(ctx, x, y, w, h, 1.2); ctx.stroke();
    ctx.fillStyle = '#080a10'; roundRect(ctx, x + 2, y + 2.5, w - 4, h - 5.5, 0.6); ctx.fill();
    ctx.strokeStyle = '#1f2533'; ctx.lineWidth = 0.5;
    for (let l = 0; l < 4; l++) { const lx = x + 3.5 + l * ((w - 7) / 3); ctx.beginPath(); ctx.moveTo(lx, y + 3.5); ctx.lineTo(lx, y + h - 3.5); ctx.stroke(); }
    if (isHighlight) { ctx.strokeStyle = '#ff4fa3'; ctx.lineWidth = 1.2; roundRect(ctx, x - 1.5, y - 1.5, w + 3, h + 3, 1.5); ctx.stroke(); }
    const ledCx = x + w / 2, ledCy = y - 3.8; let ledCol = isUp ? '#22c55e' : '#f7768e';
    if (isActive && (Math.floor(time * 5.5) % 2) === 0) ledCol = '#4ade80';
    ctx.fillStyle = ledCol; ctx.beginPath(); ctx.arc(ledCx, ledCy, 1.75, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.4)'; ctx.lineWidth = 0.4; ctx.beginPath(); ctx.arc(ledCx, ledCy, 1.75, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = isHighlight ? '#d0d8ee' : '#4a516a'; ctx.font = '5px system-ui, monospace'; ctx.textAlign = 'center';
    ctx.fillText(String(num), x + w / 2, y + h + 6.5);
    ctx.fillStyle = '#3a4158'; ctx.font = '3.8px system-ui, monospace'; ctx.fillText('40G', x + w / 2, y + h + 10.2); ctx.textAlign = 'start';
  };


  const selectPort = (name: string) => { setSelected(name); };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl tracking-wide">
            SWITCH <span className="text-cyan">DCS-7050TX-48</span>
          </h1>
          <div className="text-xs text-muted metric">
            10.0.20.2 • {connected} up • {data.length} reported • canvas physical faceplate
          </div>
        </div>
        <div className="text-[10px] text-muted tracking-[2px] border border-border-token px-2 py-0.5 rounded">
          RACK 47 • TOKYO NIGHT
        </div>
      </div>

      {(serverError || portsPoll.error) && (
        <div className="card border-red/40 p-4 text-red text-sm" role="alert">
          Switch offline or error: {serverError || portsPoll.error}
          {hasData && " — showing last known data (cached)"}
          <div className="text-[10px] text-muted mt-1">check eAPI (management api http-commands), creds, network. Polling continues.</div>
        </div>
      )}
      {!hasData && !portsPoll.loading && (
        <ErrorState message="no switch ports (unconfigured or all filtered). See backend logs." />
      )}

      <div className="flex flex-col lg:flex-row gap-6">
        <div className="flex-1 min-w-0">
          <div className="relative mx-[22px]">
            <div className="rack-ear rack-ear-left" />
            <div className="rack-ear rack-ear-right" />
            <div className="rack-bezel">
              <div className="faceplate-wrapper">
                {renderFaceplate()}
              </div>
            </div>
          </div>
          <div className="mt-1.5 px-1 flex items-center justify-between text-[10px] text-muted tracking-widest">
            <span>click ports • green=up / red=down • blink=activity (&gt;1 kbps)</span>
            <span>48×10G-T RJ45 + 4×40G QSFP+</span>
          </div>
        </div>

        <div className="w-full lg:w-80 flex-shrink-0 space-y-4">
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

          <div className="card p-3 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-xs uppercase tracking-[1px] text-muted">PORT DETAILS</div>
              {selected && (
                <button className="btn-plain text-[10px] px-1.5 py-px" onClick={() => { setSelected(null); setEditing(null); }}>× close</button>
              )}
            </div>

            {!selectedPort ? (
              <div className="py-6 text-center text-xs text-muted italic">select a port from the faceplate or top talkers list</div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <div className="font-mono text-[15px] tracking-wider text-fg">{selectedPort.name}</div>
                  <div className={`w-2.5 h-2.5 rounded-full ${selectedPort.status === "connected" ? "bg-[#22c55e]" : "bg-red"} ${selectedPort.active ? "led-active" : ""}`} title={selectedPort.status} />
                </div>

                <div className="grid grid-cols-2 gap-x-4 gap-y-[1px] text-[10px]">
                  <div className="text-muted">speed <span className="text-fg tabular-nums">{selectedPort.speed || "—"}</span></div>
                  <div className="text-muted">duplex <span className="text-fg">{selectedPort.duplex || "—"}</span></div>
                  <div className="text-muted">vlan <span className="text-fg">{selectedPort.vlan || "—"}</span></div>
                  <div className="text-muted">state <span className="text-fg">{selectedPort.status}</span></div>
                </div>

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

                  <div>
                    <div className="uppercase tracking-wider text-[10px] text-muted mb-px">NOTES <span className="normal-case text-pink/60">(local to panel)</span></div>
                    {editing === selectedPort.name ? (
                      <div className="space-y-1">
                        <input
                          className="input text-xs py-1"
                          value={noteDraft}
                          onChange={(e) => {
                            const v = e.target.value; setNoteDraft(v);
                            if (editing === selectedPort.name) scheduleDebouncedSave(selectedPort.name, v);
                          }}
                          placeholder="your note…"
                          onKeyDown={(e) => { if (e.key === "Enter") void saveNote(selectedPort.name); if (e.key === "Escape") setEditing(null); }}
                          onBlur={() => { if (editing === selectedPort.name && noteDraft.trim() !== (selectedPort.note || "")) void saveNote(selectedPort.name); }}
                          autoFocus
                        />
                        <div className="flex gap-1.5">
                          <button className="btn-plain text-[10px] px-2 py-px" onClick={() => setEditing(null)}>cancel</button>
                          <button className="btn-cyan text-[10px] px-2 py-px" onClick={() => void saveNote(selectedPort.name)}>save</button>
                        </div>
                      </div>
                    ) : (
                      <div className="group flex justify-between items-start gap-2 cursor-pointer py-0.5 rounded hover:bg-border-token/20 -mx-1 px-1" onClick={() => { setEditing(selectedPort.name); setNoteDraft(selectedPort.note || ""); }}>
                        <span className={selectedPort.note ? "text-fg" : "text-muted italic group-hover:text-pink"}>{selectedPort.note || "click to add note"}</span>
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
        faceplate emulates physical Arista DCS-7050TX-48 (canvas) • 48×10GBASE-T RJ45 (2 rows) + 4×40G QSFP+ • left mgmt/console/USB + LEDs • high-DPI • clickable + LLDP + notes
      </div>
    </div>
  );
}

function formatRate(bps: number): string {
  if (!bps) return "0";
  if (bps < 1_000_000) return (bps / 1000).toFixed(0) + "k";
  return (bps / 1_000_000).toFixed(1) + "M";
}
