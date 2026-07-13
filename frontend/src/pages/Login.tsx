import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, type SessionInfo } from "../api";
import { Wordmark } from "../components/Layout";

export function Login({ onLogin }: { onLogin: (s: SessionInfo) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await login(username.trim(), password);
      onLogin(s);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Thesis: the high seat, from which all realms are seen. */}
        <div className="reveal text-center mb-8" style={{ ["--step" as string]: 0 }}>
          <div className="eyebrow justify-center mb-4">Proxmox control</div>
          <Wordmark className="text-[44px] leading-none" />
          {/* Deliberately names no host: this renders before authentication, and
              the node a panel watches is not something to hand out to strangers. */}
          <p className="text-muted text-sm mt-4 max-w-xs mx-auto leading-relaxed">
            The high seat. From it, one watches over every guest in the fleet.
          </p>
        </div>

        <form
          onSubmit={submit}
          className="reveal card login-card p-6 space-y-4"
          style={{ ["--step" as string]: 1 }}
        >
          <div>
            <label className="label" htmlFor="username">
              username
            </label>
            <input
              id="username"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              autoFocus
              required
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              password
            </label>
            <input
              id="password"
              type="password"
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          {error && (
            <div className="text-red text-xs" role="alert">
              {error}
            </div>
          )}
          <button type="submit" className="btn-pink w-full" disabled={busy}>
            {busy ? "taking the seat…" : "take the seat"}
          </button>
        </form>

        <p
          className="reveal text-center text-[10px] text-muted uppercase tracking-eyebrow mt-6"
          style={{ ["--step" as string]: 2 }}
        >
          Proxmox control panel
        </p>
      </div>
    </div>
  );
}
