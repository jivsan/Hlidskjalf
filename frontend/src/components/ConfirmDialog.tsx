import { useEffect, useState, type ReactNode } from "react";

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  confirmClass = "btn-red",
  requireText,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  confirmClass?: string;
  /** When set, the user must type this exact string to enable confirm. */
  requireText?: string;
  busy?: boolean;
  onConfirm: (typedText: string) => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");

  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  if (!open) return null;

  const ok = requireText == null || typed === requireText;

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-bg/80 p-4"
      onClick={onCancel}
    >
      <div
        className="card p-5 w-full max-w-md"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base mb-3">{title}</h2>
        {children && <div className="text-sm text-muted mb-4 space-y-2">{children}</div>}
        {requireText != null && (
          <div className="mb-4">
            <label className="label">
              type <span className="text-fg">{requireText}</span> to confirm
            </label>
            <input
              className="input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus
              spellCheck={false}
              autoComplete="off"
            />
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button className="btn-plain" onClick={onCancel} disabled={busy}>
            cancel
          </button>
          <button
            className={confirmClass}
            disabled={!ok || busy}
            onClick={() => onConfirm(typed)}
          >
            {busy ? "working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
