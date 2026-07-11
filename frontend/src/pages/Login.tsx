import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      onLogin();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <form onSubmit={submit} className="card p-6 w-full max-w-sm space-y-4">
        <div className="text-center text-lg">
          <span className="text-pink">hlid</span>
          <span className="text-cyan">skjalf</span>
        </div>
        <p className="text-center text-xs text-muted">the high seat overlooking hella</p>
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
    </div>
  );
}
