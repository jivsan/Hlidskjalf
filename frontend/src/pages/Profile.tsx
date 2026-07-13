import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { changeOwnPassword } from "../api";
import type { CurrentUser } from "../App";
import { Card, PageHeader } from "../components/ui";

const MIN_PASSWORD = 8;

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-red text-xs mt-1">{error}</p>
      ) : hint ? (
        <p className="text-muted text-xs mt-1">{hint}</p>
      ) : null}
    </div>
  );
}

/** One account line: label left, value right. */
function FactRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-muted text-xs shrink-0">{label}</span>
      <span className="text-sm text-fg text-right min-w-0">{value}</span>
    </div>
  );
}

export function Profile({ currentUser }: { currentUser: CurrentUser }) {
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const newPwShort = newPw !== "" && newPw.length < MIN_PASSWORD;
  const mismatch = newPw2 !== "" && newPw2 !== newPw;
  const valid =
    currentPw !== "" && newPw.length >= MIN_PASSWORD && newPw2 === newPw;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await changeOwnPassword(currentUser.username, currentPw, newPw);
      setDone(true);
      setCurrentPw("");
      setNewPw("");
      setNewPw2("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "password change failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6 max-w-lg">
      <PageHeader eyebrow="account" title={currentUser.username} />

      <Card title="Account">
        <div className="divide-y divide-border-token/50">
          <FactRow label="username" value={<span className="metric">{currentUser.username}</span>} />
          <FactRow
            label="role"
            value={
              <span
                className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border ${
                  currentUser.role === "admin"
                    ? "text-pink border-pink/40 bg-pink/5"
                    : "text-cyan border-cyan/30 bg-cyan/5"
                }`}
              >
                {currentUser.role}
              </span>
            }
          />
          {currentUser.role === "user" && (
            <FactRow
              label="your VM"
              value={
                currentUser.vmid != null ? (
                  <Link
                    to={`/vm/${currentUser.vmid}`}
                    className="metric text-cyan hover:underline"
                  >
                    vm {currentUser.vmid}
                  </Link>
                ) : (
                  <span className="text-muted">none assigned</span>
                )
              }
            />
          )}
        </div>
      </Card>

      <Card title="Change password">
        <form onSubmit={submit} className="space-y-3">
          <Field id="pw-current" label="current password">
            <input
              id="pw-current"
              type="password"
              className="input"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          <Field
            id="pw-new"
            label="new password"
            error={newPwShort ? `at least ${MIN_PASSWORD} characters` : null}
            hint={`at least ${MIN_PASSWORD} characters`}
          >
            <input
              id="pw-new"
              type="password"
              className="input"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
              required
            />
          </Field>
          <Field
            id="pw-new2"
            label="confirm new password"
            error={mismatch ? "the two passwords do not match" : null}
          >
            <input
              id="pw-new2"
              type="password"
              className="input"
              value={newPw2}
              onChange={(e) => setNewPw2(e.target.value)}
              autoComplete="new-password"
              required
            />
          </Field>

          {error && (
            <p className="text-red text-sm" role="alert">
              {error}
            </p>
          )}
          {done && !error && (
            <p className="text-muted text-sm" role="status">
              password changed — every other session was signed out
            </p>
          )}

          <button type="submit" className="btn-pink" disabled={!valid || busy}>
            {busy ? "changing…" : "change password"}
          </button>
        </form>
      </Card>
    </div>
  );
}
