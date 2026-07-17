import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import RFB from "@novnc/novnc";
import { api } from "../../api";
import { useToast } from "../../components/Toast";
import { Card } from "../../components/ui";
import { cssVar } from "../../lib/theme";
import type { ConsoleInfo, GuestKind } from "../../types";

// xterm.js is only ever needed for containers — keep it out of the VM path's bundle.
const TerminalConsole = lazy(() =>
  import("./TerminalConsole").then((m) => ({ default: m.TerminalConsole })),
);

type ConnState = "disconnected" | "connecting" | "connected";

const STATE_LABEL: Record<ConnState, string> = {
  disconnected: "disconnected",
  connecting: "connecting…",
  connected: "connected",
};

/** Containers get a terminal (termproxy); VMs get a framebuffer (noVNC). Proxmox
 *  serves no working VNC for LXC — its RFB handshake hangs at ClientInit. */
export function ConsoleTab({ vmid, kind }: { vmid: number; kind: GuestKind }) {
  if (kind === "lxc") return <LxcConsole vmid={vmid} />;
  return <VncConsole vmid={vmid} />;
}

function LxcConsole({ vmid }: { vmid: number }) {
  const [state, setState] = useState<ConnState>("connecting");
  const [lastError, setLastError] = useState<string | null>(null);
  const onState = useCallback((s: ConnState, err?: string | null) => {
    setState(s);
    if (err !== undefined) setLastError(err);
  }, []);

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="eyebrow">container terminal</span>
        <span
          className={`ml-auto text-xs metric ${
            state === "connected" ? "text-cyan" : state === "connecting" ? "text-amber" : "text-muted"
          }`}
        >
          ● {STATE_LABEL[state]}
        </span>
      </div>
      {lastError && <div className="text-red text-xs mb-2">{lastError}</div>}
      <Suspense
        fallback={
          <div
            className="well w-full flex items-center justify-center text-muted text-sm"
            style={{ height: "60vh", minHeight: 320 }}
          >
            loading terminal…
          </div>
        }
      >
        <TerminalConsole vmid={vmid} onState={onState} />
      </Suspense>
    </Card>
  );
}

function VncConsole({ vmid }: { vmid: number }) {
  const toast = useToast();
  const screenRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [state, setState] = useState<ConnState>("disconnected");
  const [lastError, setLastError] = useState<string | null>(null);

  const disconnect = useCallback(() => {
    if (rfbRef.current) {
      try {
        rfbRef.current.disconnect();
      } catch {
        /* already down */
      }
      rfbRef.current = null;
    }
    setState("disconnected");
  }, []);

  const connect = useCallback(async () => {
    if (rfbRef.current || !screenRef.current) return;
    setState("connecting");
    setLastError(null);
    try {
      const info = await api.get<ConsoleInfo>(`/api/vms/${vmid}/console`);
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${window.location.host}${info.ws_path}`;
      const rfb = new RFB(screenRef.current, url, {
        credentials: { password: info.password },
      });
      rfb.scaleViewport = true;
      rfb.background = cssVar("--c-bg", "#15161f");
      rfb.addEventListener("connect", () => setState("connected"));
      rfb.addEventListener("disconnect", (ev) => {
        rfbRef.current = null;
        setState("disconnected");
        if (!ev.detail.clean) {
          setLastError("connection lost unexpectedly");
        }
      });
      rfb.addEventListener("credentialsrequired", () => {
        rfb.sendCredentials({ password: info.password });
      });
      rfb.addEventListener("securityfailure", (ev) => {
        setLastError(`security failure: ${ev.detail.reason ?? ev.detail.status}`);
      });
      rfbRef.current = rfb;
    } catch (e) {
      setState("disconnected");
      const msg = e instanceof Error ? e.message : "console unavailable";
      setLastError(msg);
      toast.error(msg);
    }
  }, [vmid, toast]);

  // Auto-connect on mount, clean up on unmount.
  useEffect(() => {
    void connect();
    return () => {
      if (rfbRef.current) {
        try {
          rfbRef.current.disconnect();
        } catch {
          /* noop */
        }
        rfbRef.current = null;
      }
    };
  }, [connect]);

  const sendCad = () => {
    rfbRef.current?.sendCtrlAltDel();
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button className="btn-cyan" onClick={() => void connect()} disabled={state !== "disconnected"}>
          connect
        </button>
        <button className="btn-plain" onClick={disconnect} disabled={state === "disconnected"}>
          disconnect
        </button>
        <button className="btn-plain" onClick={sendCad} disabled={state !== "connected"}>
          ctrl-alt-del
        </button>
        <span
          className={`ml-auto text-xs metric ${
            state === "connected" ? "text-cyan" : state === "connecting" ? "text-amber" : "text-muted"
          }`}
        >
          ● {STATE_LABEL[state]}
        </span>
      </div>
      {lastError && <div className="text-red text-xs mb-2">{lastError}</div>}
      <div
        ref={screenRef}
        className="well w-full overflow-hidden"
        style={{ height: "60vh", minHeight: 320 }}
      >
        {state === "disconnected" && !lastError && (
          <div className="h-full flex items-center justify-center text-muted text-sm">
            console disconnected
          </div>
        )}
      </div>
    </Card>
  );
}
