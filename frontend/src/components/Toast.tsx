import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastKind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastContextValue {
  toast: (kind: ToastKind, message: string) => void;
  success: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}

const KIND_CLASSES: Record<ToastKind, string> = {
  success: "border-cyan/60 text-cyan",
  error: "border-red/60 text-red",
  info: "border-border-token text-fg",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const toast = useCallback((kind: ToastKind, message: string) => {
    const id = nextId.current++;
    // Cap the stack: a poll loop erroring every few seconds must not fill the
    // screen — drop the oldest beyond 5.
    setToasts((ts) => [...ts, { id, kind, message }].slice(-5));
    window.setTimeout(() => {
      setToasts((ts) => ts.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (m: string) => toast("success", m),
      error: (m: string) => toast("error", m),
    }),
    [toast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`card px-4 py-3 shadow-lg bg-surface text-sm break-words ${KIND_CLASSES[t.kind]}`}
            onClick={() => setToasts((ts) => ts.filter((x) => x.id !== t.id))}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
