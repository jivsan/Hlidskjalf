import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, type SessionInfo } from "../api";
import { Wordmark } from "../components/Layout";

// The one typed line in the entire app. Real copy; names no host — this page
// renders before authentication. Anything more would be a theme park.
const BOOT_LINE = "> the high seat — watching every guest";

/** Types `text` once at human speed. Any key/click completes it instantly;
    prefers-reduced-motion renders it complete from the start. */
function useTypewriter(text: string, speed = 34) {
  const reduced =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [n, setN] = useState(reduced ? text.length : 0);
  const done = n >= text.length;
  useEffect(() => {
    if (done) return;
    const id = window.setInterval(() => setN((v) => Math.min(v + 1, text.length)), speed);
    const skip = () => setN(text.length);
    window.addEventListener("keydown", skip, { once: true });
    window.addEventListener("pointerdown", skip, { once: true });
    return () => {
      window.clearInterval(id);
      window.removeEventListener("keydown", skip);
      window.removeEventListener("pointerdown", skip);
    };
  }, [done, text.length, speed]);
  return { shown: text.slice(0, n), done };
}

export function Login({ onLogin }: { onLogin: (s: SessionInfo) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const boot = useTypewriter(BOOT_LINE);
  // The cursor blinks while typing and retires a couple of beats after the
  // line settles — it does not haunt the page forever.
  const [cursorRetired, setCursorRetired] = useState(false);
  useEffect(() => {
    if (!boot.done) return;
    const id = window.setTimeout(() => setCursorRetired(true), 2200);
    return () => window.clearTimeout(id);
  }, [boot.done]);

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
        {/* Thesis: the high seat, from which all realms are seen. The sign
            ignites once (neon-ignite), then burns steady. */}
        <div className="neon-ignite text-center mb-8">
          <div className="eyebrow justify-center mb-4">Proxmox control</div>
          <Wordmark className="text-[44px] leading-none" />
          {/* The one typed line. aria-label carries the full text; the animated
              span is decorative. */}
          <p
            className="metric text-muted text-xs mt-5 h-4"
            aria-label={BOOT_LINE}
          >
            <span aria-hidden="true">
              {boot.shown}
              {!cursorRetired && <span className="boot-cursor text-cyan">▌</span>}
            </span>
          </p>
        </div>

        {/* The hero card: corner brackets always on, powers on like a CRT. */}
        <form
          onSubmit={submit}
          className="power-on card login-card card-brackets p-6 space-y-4"
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
