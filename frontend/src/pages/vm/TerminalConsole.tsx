import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { api } from "../../api";
import type { ConsoleInfo } from "../../types";

// Containers have no framebuffer. Proxmox drives them through `termproxy` — a
// line-framed terminal protocol over the same websocket endpoint — because an
// LXC guest's VNC endpoint completes the RFB handshake and then hangs at
// ClientInit (verified against PVE 9.2.3). So a container's console is a real
// terminal, not a screen.
//
// The wire format, browser -> PVE:
//   "0:<byte-length>:<data>"   keystrokes
//   "1:<cols>:<rows>:"         resize
//   "2"                        keepalive (PVE hangs up on a silent client)
// PVE -> browser is raw terminal output. The auth line ("<user>:<ticket>") is
// sent by the PANEL, not here — the container's ticket never reaches the browser.

const ENC = new TextEncoder();
const KEEPALIVE_MS = 30_000;

type ConnState = "connecting" | "connected" | "disconnected";

export function TerminalConsole({
  vmid,
  onState,
}: {
  vmid: number;
  onState: (s: ConnState, error?: string | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let disposed = false;
    let keepalive: number | undefined;

    const term = new Terminal({
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", ui-monospace, monospace',
      fontSize: 13,
      // Matches the design system's recessed surfaces; the terminal IS machine data.
      theme: { background: "#12131c", foreground: "#c8cce0", cursor: "#7de3f4" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    termRef.current = term;

    const start = async () => {
      onState("connecting");
      try {
        const info = await api.get<ConsoleInfo>(`/api/vms/${vmid}/console`);
        if (disposed || !hostRef.current) return;

        term.open(hostRef.current);
        fit.fit();
        setReady(true);

        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${proto}://${window.location.host}${info.ws_path}`, "binary");
        wsRef.current = ws;

        ws.onopen = () => {
          onState("connected");
          ws.send(`1:${term.cols}:${term.rows}:`);
          keepalive = window.setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send("2");
          }, KEEPALIVE_MS);
        };
        ws.onmessage = async (ev) => {
          const data =
            ev.data instanceof Blob ? new Uint8Array(await ev.data.arrayBuffer()) : ev.data;
          term.write(typeof data === "string" ? data : data);
        };
        ws.onerror = () => onState("disconnected", "connection failed");
        ws.onclose = (ev) => {
          onState(
            "disconnected",
            ev.code === 4403
              ? "the console key was rejected"
              : ev.code === 4401
                ? "your session expired — sign in again"
                : ev.code === 4502
                  ? "Proxmox did not answer the terminal handshake"
                  : null,
          );
        };

        term.onData((data) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(`0:${ENC.encode(data).length}:${data}`);
          }
        });
        term.onResize(({ cols, rows }) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(`1:${cols}:${rows}:`);
        });
      } catch (e) {
        onState("disconnected", e instanceof Error ? e.message : "console unavailable");
      }
    };
    void start();

    const onWindowResize = () => {
      try {
        fit.fit();
      } catch {
        /* not attached yet */
      }
    };
    window.addEventListener("resize", onWindowResize);

    return () => {
      disposed = true;
      window.clearInterval(keepalive);
      window.removeEventListener("resize", onWindowResize);
      wsRef.current?.close();
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
    };
  }, [vmid, onState]);

  return (
    <div
      ref={hostRef}
      className="well w-full overflow-hidden p-2"
      style={{ height: "60vh", minHeight: 320 }}
    >
      {!ready && (
        <div className="h-full flex items-center justify-center text-muted text-sm">
          opening terminal…
        </div>
      )}
    </div>
  );
}
