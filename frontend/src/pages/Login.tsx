import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, type SessionInfo } from "../api";

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
    <div className="min-h-screen flex items-center justify-center p-4 login-backdrop">
      <div className="w-full max-w-sm">
        <form onSubmit={submit} className="card login-card p-6 space-y-4">
          <div className="text-center space-y-1 pb-1">
            <div className="text-2xl font-medium tracking-wide">
              <span className="text-pink">hlid</span>
              <span className="text-cyan">skjalf</span>
            </div>
            <p className="text-xs text-muted tracking-widest uppercase">
              the high seat overlooking hella
            </p>
          </div>
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
            {busy ? "authenticating…" : "login"}
          </button>
        </form>
        <p className="text-center text-[10px] text-muted tracking-widest mt-4">
          HLIDSKJALF · PROXMOX CONTROL PANEL
        </p>
      </div>
    </div>
  );
}
