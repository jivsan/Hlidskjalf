import { useState, useRef, useCallback, useEffect } from "react";
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

  // Poll returns the new {ports, error?} shape. usePoll preserves .data on errors (last-known).
  const portsPoll = usePoll(
    () => api.get<SwitchPortsResponse>("/api/switch/ports"),
    4000
  );

  // Debounced auto-save for notes (robustness requirement).
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

  // hovered state for React ports (handlers + tooltips; visual hover/LEDs are CSS)
  const [_hovered, setHovered] = useState<string | null>(null);

  const selectPort = (name: string) => {
    setSelected(name);
  };

  // Small declarative React port components (DOM + Tailwind/CSS). No Canvas/SVG.
  // Matches real Arista DCS-7050TX-48 1U: dark metal, recessed RJ45 (latch notch + contacts), LED above, QSFP cages w/ 4 lanes, rack ears, vents, labels.
  // Props support full functionality: onClick for selected+details panel, active blink, LLDP in title, hover.
  // Robust: defaults + graceful no-data (ports render "down").

  interface PortProps {
    name: string;
    status?: string;
    active?: boolean;
    selected?: boolean;
    lldpNeighbor?: { system_name?: string; port?: string } | null;
    onClick: (name: string) => void;
    onHover: (name: string | null) => void;
  }

  const Rj45Port: React.FC<PortProps> = ({ name, status = "disconnected", active = false, selected = false, lldpNeighbor, onClick, onHover }) => {
    const isUp = status === "connected";
    const num = parseInt(name.replace("Ethernet", ""), 10) || 0;
    const lldpText = lldpNeighbor?.system_name ? ` (LLDP: ${lldpNeighbor.system_name}${lldpNeighbor.port ? " " + lldpNeighbor.port : ""})` : "";
    return (
      <button
        type="button"
        onClick={() => onClick(name)}
        onMouseEnter={() => onHover(name)}
        onMouseLeave={() => onHover(null)}
        className={`rj45-port ${isUp ? "up" : "down"} ${active ? "active" : ""} ${selected ? "selected" : ""}`}
        aria-label={`RJ45 port ${num} ${isUp ? "up" : "down"}${active ? ", active traffic" : ""}${lldpText}`}
        title={`${name} — ${isUp ? "connected" : "not connected"}${lldpText}`}
      >
        <div className="port-led" />
        <div className="jack">
          <div className="recess">
            <div className="contacts">{Array.from({ length: 8 }).map((_, i) => <span key={i} />)}</div>
          </div>
        </div>
        <span className="port-num">{num}</span>
      </button>
    );
  };

  const QsfpPort: React.FC<PortProps> = ({ name, status = "disconnected", active = false, selected = false, lldpNeighbor, onClick, onHover }) => {
    const isUp = status === "connected";
    const num = parseInt(name.replace("Ethernet", ""), 10) || 0;
    const lldpText = lldpNeighbor?.system_name ? ` (LLDP: ${lldpNeighbor.system_name}${lldpNeighbor.port ? " " + lldpNeighbor.port : ""})` : "";
    return (
      <button
        type="button"
        onClick={() => onClick(name)}
        onMouseEnter={() => onHover(name)}
        onMouseLeave={() => onHover(null)}
        className={`qsfp-port ${isUp ? "up" : "down"} ${active ? "active" : ""} ${selected ? "selected" : ""}`}
        aria-label={`QSFP port ${num} ${isUp ? "up" : "down"}${active ? ", active" : ""}${lldpText}`}
        title={`${name} — ${isUp ? "connected" : "not connected"}${lldpText} (40G)`}
      >
        <div className="port-led" />
        <div className="cage">
          <div className="slot">
            <div className="lanes">{Array.from({ length: 4 }).map((_, i) => <span key={i} />)}</div>
          </div>
        </div>
        <span className="port-num">{num}</span>
        <span className="qsfp-speed">40G</span>
      </button>
    );
  };

  // The physical faceplate: exact 2 rows of 24 RJ45 + 4 QSFP stacked right. Left mgmt + labels + vents + ears via outer CSS.
  const renderReactFaceplate = () => {
    const copperPorts: React.ReactNode[] = [];
    for (let i = 1; i <= 48; i++) {
      const name = `Ethernet${i}`;
      const pd = portMap.get(name);
      const isSel = selected === name;
      copperPorts.push(
        <Rj45Port
          key={name}
          name={name}
          status={pd?.status}
          active={pd?.active}
          selected={isSel}
          lldpNeighbor={pd?.lldpNeighbor}
          onClick={selectPort}
          onHover={setHovered}
        />
      );
    }
    const qsfpPorts = [49, 50, 51, 52].map((num) => {
      const name = `Ethernet${num}`;
      const pd = portMap.get(name);
      const isSel = selected === name;
      return (
        <QsfpPort
          key={name}
          name={name}
          status={pd?.status}
          active={pd?.active}
          selected={isSel}
          lldpNeighbor={pd?.lldpNeighbor}
          onClick={selectPort}
          onHover={setHovered}
        />
      );
    });

    return (
      <div className="arista-chassis" role="img" aria-label="Arista DCS-7050TX-48 front panel - realistic physical 1U using React components and CSS">
        <div className="arista-inner">
          <div className="vents top" />
          <div className="vents bottom" />

          <div className="mgmt-area">
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}>
              <div className="mgmt-port" title="Console" /><span className="mgmt-label">CON</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}>
              <div className="mgmt-port usb" title="USB" /><span className="mgmt-label">USB</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}>
              <div className="mgmt-port" title="Management" /><span className="mgmt-label">MGMT</span>
            </div>
          </div>
          <div className="status-leds">
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}><div className="status-led sys" title="SYS" /><span className="status-label">SYS</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}><div className="status-led fan" title="FAN" /><span className="status-label">FAN</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}><div className="status-led ps" title="PS1" /><span className="status-label">PS1</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "3px" }}><div className="status-led ps" title="PS2" /><span className="status-label">PS2</span></div>
          </div>

          <div className="model-label">
            ARISTA
            <span className="model">DCS-7050TX-48</span>
            <span className="spec">48×10GBASE-T + 4×40GbE QSFP+</span>
          </div>
          <div className="row-label top">1-24</div>
          <div className="row-label bottom">25-48</div>

          <div className="ports-area">
            <div className="ports-grid-copper">{copperPorts}</div>
            <div className="ports-grid-qsfp">{qsfpPorts}</div>
          </div>

          <div className="chassis-footer">RACK 47 • DCS-7050TX-48</div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl tracking-wide">
            SWITCH <span className="text-cyan">DCS-7050TX-48</span>
          </h1>
          <div className="text-xs text-muted metric">
            10.0.20.2 • {connected} up • {data.length} reported • React physical faceplate
          </div>
        </div>
        <div className="text-[10px] text-muted tracking-[2px] border border-border-token px-2 py-0.5 rounded">
          RACK 47 • TOKYO NIGHT
        </div>
      </div>

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
        {/* Realistic physical 1U Arista faceplate - React declarative (divs/buttons) + Tailwind/CSS. Non-SVG. */}
        <div className="flex-1 min-w-0">
          <div className="relative mx-[22px]">
            <div className="rack-ear rack-ear-left"><div className="screw" /><div className="screw" /></div>
            <div className="rack-ear rack-ear-right"><div className="screw" /><div className="screw" /></div>
            <div className="rack-bezel">
              <div className={`faceplate-wrapper ${!hasData ? "opacity-60" : ""} ${portsPoll.loading ? "loading" : ""}`}>
                {renderReactFaceplate()}
              </div>
            </div>
          </div>
          <div className="mt-1.5 px-1 flex items-center justify-between text-[10px] text-muted tracking-widest">
            <span>click ports • green=up / red=down • blink=activity (&gt;1 kbps)</span>
            <span>48×10G-T RJ45 (2 rows of 24) + 4×40G QSFP+ on right</span>
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
                          onChange={(e) => {
                            const v = e.target.value;
                            setNoteDraft(v);
                            if (editing === selectedPort.name) scheduleDebouncedSave(selectedPort.name, v);
                          }}
                          placeholder="your note…"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void saveNote(selectedPort.name);
                            if (e.key === "Escape") setEditing(null);
                          }}
                          onBlur={() => {
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
        React faceplate • physical Arista DCS-7050TX-48 (metal bevels, recessed RJ45 w/ LED, QSFP cages, rack ears, vents) • exact 48+4 layout • clickable + LLDP + notes + live blink
      </div>
    </div>
  );
}

function formatRate(bps: number): string {
  if (!bps) return "0";
  if (bps < 1_000_000) return (bps / 1000).toFixed(0) + "k";
  return (bps / 1_000_000).toFixed(1) + "M";
}
