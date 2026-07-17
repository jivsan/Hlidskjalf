import { useState, useRef, useCallback, useEffect } from "react";
import { api } from "../api";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { useToast } from "../components/Toast";
import { ErrorState, LoadingState, PageHeader } from "../components/ui";
import { usePoll } from "../hooks/usePoll";
import { formatRate } from "../lib/format";
import type { SwitchPort, SwitchPortsResponse } from "../types";

// Port alias for brevity (matches backend PortInfo serialized).
type Port = SwitchPort;

// eAPI reports inBitsRate/outBitsRate (bits/sec); the shared formatter speaks
// bytes/sec like every other rate in the panel (VM net graphs, node traffic).
const formatBitsPerSec = (bps: number) => formatRate(bps / 8);

export function SwitchPage() {
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [hovered, setHovered] = useState<string | null>(null);

  // Poll returns the new {ports, error?} shape. usePoll preserves .data on errors (last-known).
  const portsPoll = usePoll(
    () => api.get<SwitchPortsResponse>("/api/switch/ports"),
    4000
  );

  // Debounced auto-save for notes (robustness requirement).
  // 650ms after typing stops while editing; explicit save also available via Enter.
  // Never blocks UI; errors non-fatal on auto path.
  const saveTimerRef = useRef<number | null>(null);
  const scheduleDebouncedSave = useCallback((name: string, note: string) => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(async () => {
      try {
        await api.post(`/api/switch/ports/${encodeURIComponent(name)}/note`, { note: note.trim() });
        portsPoll.refresh();
      } catch (e) {
        /* debounce auto-save is best-effort */
      }
    }, 650);
  }, [portsPoll]);

  const saveNote = async (name: string) => {
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    const noteVal = noteDraft.trim();
    try {
      await api.post(`/api/switch/ports/${encodeURIComponent(name)}/note`, {
        note: noteVal,
      });
      toast.success(`note saved for ${name}`);
      setEditing(null);
      portsPoll.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "failed to save note");
    }
  };

  // ensure timer cleared
  useEffect(() => () => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
  }, []);

  // data handling: support both old array shape (compat) and new {ports, error}
  const response = portsPoll.data as SwitchPortsResponse | Port[] | null;
  const data: Port[] = Array.isArray(response)
    ? response
    : response && "ports" in response
      ? response.ports
      : [];
  const serverError: string | null =
    !Array.isArray(response) && response && "error" in response
      ? (response.error as string) || null
      : portsPoll.error;
  const hasData = data.length > 0;

  if (portsPoll.loading && !hasData) {
    return <LoadingState message="connecting to switch…" />;
  }

  const connected = data.filter((p) => p.status === "connected").length;
  const portMap = new Map(data.map((p) => [p.name, p] as const));

  const topTalkers = [...data]
    .sort((a, b) => (b.inputRate + b.outputRate) - (a.inputRate + a.outputRate))
    .slice(0, 5);

  const selectedPort = selected ? portMap.get(selected) : null;

  const selectPort = (name: string) => {
    setSelected(name);
  };

  const renderReactFaceplate = () => {
    const copperTop = Array.from({ length: 24 }, (_, i) => `Ethernet${i + 1}`);
    const copperBottom = Array.from({ length: 24 }, (_, i) => `Ethernet${i + 25}`);
    const qsfps = [49, 50, 51, 52].map((n) => `Ethernet${n}`);

    const renderRJ45 = (name: string) => {
      const p = portMap.get(name);
      const isUp = p?.status === "connected";
      const isActive = !!p?.active;
      const isSelected = selected === name;
      const num = name.replace("Ethernet", "");
      const lldp = p?.lldpNeighbor;
      const title = `${name} • ${isUp ? "UP" : "DOWN"} ${p?.speed || ""} ${p?.description ? "• " + p.description : ""}${lldp ? " • LLDP:" + (lldp.system_name || "") : ""}`.trim();

      return (
        <div
          key={name}
          className={`rj45-port ${isUp ? "up" : "down"} ${isActive ? "active" : ""} ${isSelected ? "selected" : ""} ${hovered === name ? "hovered" : ""}`}
          onClick={() => selectPort(name)}
          onMouseEnter={() => setHovered(name)}
          onMouseLeave={() => setHovered(null)}
          title={title}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectPort(name);
            }
          }}
        >
          <div className="port-led" />
          <div className="jack">
            <div className="recess">
              <div className="contacts">
                {Array.from({ length: 8 }).map((_, k) => (
                  <span key={k} />
                ))}
              </div>
            </div>
          </div>
          <div className="port-num">{num}</div>
        </div>
      );
    };

    const renderQSFP = (name: string) => {
      const p = portMap.get(name);
      const isUp = p?.status === "connected";
      const isActive = !!p?.active;
      const isSelected = selected === name;
      const num = name.replace("Ethernet", "");
      const lldp = p?.lldpNeighbor;
      const title = `${name} (QSFP+ 40G) • ${isUp ? "UP" : "DOWN"}${lldp ? " • " + (lldp.system_name || "") : ""}`;

      return (
        <div
          key={name}
          className={`qsfp-port ${isUp ? "up" : ""} ${isActive ? "active" : ""} ${isSelected ? "selected" : ""} ${hovered === name ? "hovered" : ""}`}
          onClick={() => selectPort(name)}
          onMouseEnter={() => setHovered(name)}
          onMouseLeave={() => setHovered(null)}
          title={title}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectPort(name);
            }
          }}
        >
          <div className="port-led" />
          <div className="cage">
            <div className="slot" />
            <div className="lanes">
              {Array.from({ length: 4 }).map((_, k) => (
                <span key={k} />
              ))}
            </div>
          </div>
          <div className="port-num">{num}</div>
          <div className="qsfp-speed">40G</div>
        </div>
      );
    };

    return (
      <div
        className="faceplate-wrapper"
        aria-label="Arista DCS-7050TX-48 realistic 1U physical faceplate (React + CSS)"
        role="img"
      >
        <div className="arista-chassis">
          <div className="arista-inner" />

          {/* Ventilation grills */}
          <div className="vents top" />
          <div className="vents bottom" />

          {/* Left management area: CON/USB/MGMT ports */}
          <div className="mgmt-area">
            <div className="mgmt-port" title="Console (CON)" />
            <div className="mgmt-label" style={{ top: "22px" }}>CON</div>
            <div className="mgmt-port usb" title="USB" />
            <div className="mgmt-label" style={{ top: "46px" }}>USB</div>
            <div className="mgmt-port" title="MGMT Ethernet" />
            <div className="mgmt-label" style={{ top: "70px" }}>MGMT</div>
          </div>

          {/* Status LEDs (SYS/FAN/PS1/PS2) */}
          <div className="status-leds">
            <div className="status-led sys" title="System LED" />
            <div className="status-label" style={{ top: "102px" }}>SYS</div>
            <div className="status-led fan" title="Fan LED" />
            <div className="status-label" style={{ top: "112px" }}>FAN</div>
            <div className="status-led ps" title="PSU 1 LED" />
            <div className="status-label" style={{ top: "122px" }}>PS1</div>
            <div className="status-led ps" title="PSU 2 LED" />
            <div className="status-label" style={{ top: "132px" }}>PS2</div>
          </div>

          {/* Model + row labels */}
          <div className="model-label">
            <span className="model">ARISTA</span>
            <span className="spec">DCS-7050TX-48</span>
            <span className="spec" style={{ fontSize: "4.2px", marginTop: "-1px" }}>
              48×10GBASE-T + 4×40GbE QSFP+
            </span>
          </div>

          <div className="row-label top">1-24</div>
          <div className="row-label bottom">25-48</div>

          {/* Interactive ports area */}
          <div className="ports-area">
            <div className="ports-grid-copper">{copperTop.map(renderRJ45)}</div>
            <div className="ports-grid-copper">{copperBottom.map(renderRJ45)}</div>
            <div className="ports-grid-qsfp">{qsfps.map(renderQSFP)}</div>
          </div>

          <div className="chassis-footer">RACK 47 • DCS-7050TX-48</div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="network fabric"
        title="Switch"
        sub={
          <span className="metric">
            DCS-7050TX-48 · eAPI · <span className="text-cyan">{connected}</span> up · {data.length} reported
          </span>
        }
      />

      {/* Robust error/offline state: shows with last-known data if available (from usePoll) */}
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
        {/* Realistic React+CSS Physical Faceplate - 1U Arista DCS-7050TX-48 */}
        <div className="flex-1 min-w-0">
          <div className="relative mx-[22px]">
            <div className="rack-ear rack-ear-left" />
            <div className="rack-ear rack-ear-right" />
            <div className="rack-bezel">
              <ErrorBoundary
                label="faceplate"
                resetKey={data}
                fallback={
                  <div className="p-3 text-xs text-red border border-red/40 rounded">
                    faceplate failed to render — port details remain available in the sidebar
                  </div>
                }
              >
                {renderReactFaceplate()}
              </ErrorBoundary>
            </div>
          </div>
          <div className="mt-1.5 px-1 flex items-center justify-between text-[10px] text-muted tracking-widest">
            <span>click ports • green=up / red=down • blink=activity (&gt;1 kbps)</span>
            <span>48×10G-T + 4×40G QSFP+ (exact layout)</span>
          </div>
        </div>

        {/* Sidebar: Top Talkers + Enhanced Port Details */}
        <div className="w-full lg:w-80 flex-shrink-0 space-y-4">
          {/* Top Talkers */}
          <div className="card p-3">
            <div className="flex items-baseline justify-between mb-1.5">
              <div className="text-[11px] font-medium uppercase tracking-eyebrow text-muted">Top talkers</div>
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
                        <span className={`inline-block w-1.5 h-1.5 rounded-full ${isUp ? "bg-green" : "bg-red"}`} />
                      </div>
                      <span className="tabular-nums text-muted text-[10px]">{formatBitsPerSec(total)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Port Details (with desc + LLDP + inline editable notes) */}
          <div className="card p-3 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-[11px] font-medium uppercase tracking-eyebrow text-muted">Port details</div>
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
                    className={`w-2.5 h-2.5 rounded-full ${selectedPort.status === "connected" ? "bg-green" : "bg-red"} ${selectedPort.active ? "led-active" : ""}`}
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
                    <span className="tabular-nums text-fg">{formatBitsPerSec(selectedPort.inputRate)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className={`led led-pink ${selectedPort.active ? "led-active" : "led-muted"}`} />
                    <span className="text-muted">OUT</span>
                    <span className="tabular-nums text-fg">{formatBitsPerSec(selectedPort.outputRate)}</span>
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
                          onChange={(e) => {
                            const v = e.target.value;
                            setNoteDraft(v);
                            // debounce auto-save while user is actively typing note
                            if (editing === selectedPort.name) scheduleDebouncedSave(selectedPort.name, v);
                          }}
                          placeholder="your note…"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void saveNote(selectedPort.name);
                            if (e.key === "Escape") setEditing(null);
                          }}
                          onBlur={() => {
                            // immediate save on blur for good UX + robustness
                            if (editing === selectedPort.name && noteDraft.trim() !== (selectedPort.note || "")) {
                              void saveNote(selectedPort.name);
                            }
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
        faceplate emulates physical Arista DCS-7050TX-48 (React+CSS) • 48×10GBASE-T RJ45 (2 rows) + 4×40G QSFP+ • left mgmt/console/USB + LEDs • clickable + LLDP + notes
      </div>
    </div>
  );
}
