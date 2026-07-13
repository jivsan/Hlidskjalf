import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  submitSetup,
  testSetupConnection,
  type SessionInfo,
  type SetupPveConnection,
  type SetupTestResult,
} from "../api";
import { Wordmark } from "../components/Layout";
import { ErrorState } from "../components/ui";

// First run: nothing is configured and no user exists, so this wizard is the
// only reachable screen. It is Login's sibling — same hero, same calm — but it
// asks for the four things the panel cannot run without: where Proxmox lives,
// how to authenticate to it, who the operator is, and (optionally) who the
// first tenant is. On success the operator is already signed in.

const STEPS = ["proxmox", "admin", "first user", "review"] as const;
const MIN_PASSWORD = 8;
const MIN_VMID = 100;

// Proxmox prints fingerprints colon-separated; accept the bare hex form too.
const FINGERPRINT_RE = /^(?:[0-9a-f]{2}(?::[0-9a-f]{2}){31}|[0-9a-f]{64})$/i;

function StepRail({ step }: { step: number }) {
  return (
    <ol className="flex items-center justify-center gap-1.5 sm:gap-2">
      {STEPS.map((label, i) => {
        const state = i === step ? "current" : i < step ? "done" : "todo";
        const tone =
          state === "current"
            ? "border-pink/45 text-pink bg-pink/5"
            : state === "done"
              ? "border-cyan/30 text-cyan"
              : "border-border-token text-muted";
        return (
          <li key={label} className="flex items-center gap-1.5 sm:gap-2 min-w-0">
            <span
              aria-current={state === "current" ? "step" : undefined}
              className={`flex items-center gap-1.5 rounded-card border px-2 py-0.5 text-[11px] uppercase tracking-eyebrow ${tone}`}
            >
              <span className="metric">{i + 1}</span>
              <span className="hidden sm:inline">{label}</span>
              <span className="sr-only sm:hidden">{label}</span>
            </span>
            {i < STEPS.length - 1 && (
              <span className="w-3 sm:w-4 h-px bg-border-token" aria-hidden="true" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

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

/** One review line: label left, machine value right (mono). */
function ReviewRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-muted text-xs shrink-0">{label}</span>
      <span className="metric text-xs text-fg text-right break-all">{value}</span>
    </div>
  );
}

export function Setup({ onComplete }: { onComplete: (s: SessionInfo) => void }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);

  // --- Proxmox connection ---
  const [host, setHost] = useState("");
  const [port, setPort] = useState<number | "">(8006);
  const [node, setNode] = useState("pve");
  const [scheme, setScheme] = useState<"https" | "http">("https");
  const [tokenId, setTokenId] = useState("hlidskjalf@pve!panel");
  const [tokenSecret, setTokenSecret] = useState("");
  const [fingerprint, setFingerprint] = useState("");

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<SetupTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  // The test proves *these exact* values reach Proxmox. Edit any of them and the
  // proof is stale, so we re-require a test rather than let a typo through.
  const [testedFor, setTestedFor] = useState<string | null>(null);

  // --- Admin account ---
  const [adminUser, setAdminUser] = useState("");
  const [adminPw, setAdminPw] = useState("");
  const [adminPw2, setAdminPw2] = useState("");

  // --- First user (optional; skipped by default) ---
  const [wantUser, setWantUser] = useState(false);
  const [userName, setUserName] = useState("");
  const [userPw, setUserPw] = useState("");
  const [userPw2, setUserPw2] = useState("");
  const [userVmid, setUserVmid] = useState<number | "">("");

  // --- Commit ---
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [alreadyDone, setAlreadyDone] = useState(false);

  const conn: SetupPveConnection = {
    host: host.trim(),
    port: port === "" ? 0 : port,
    node: node.trim(),
    scheme,
    token_id: tokenId.trim(),
    token_secret: tokenSecret,
    fingerprint: fingerprint.trim(),
  };
  const connSignature = JSON.stringify(conn);
  const tested = testedFor !== null && testedFor === connSignature;

  const portOk = port !== "" && port >= 1 && port <= 65535;
  const fingerprintOk = conn.fingerprint === "" || FINGERPRINT_RE.test(conn.fingerprint);
  // The panel pins the certificate — there is no unpinned https, so with https
  // the fingerprint is required, not optional.
  const fingerprintFilled = scheme === "http" || conn.fingerprint !== "";
  const connFilled =
    conn.host !== "" && portOk && conn.node !== "" && conn.token_id !== "" &&
    conn.token_secret !== "" && fingerprintOk && fingerprintFilled;

  const adminPwShort = adminPw !== "" && adminPw.length < MIN_PASSWORD;
  const adminPwMismatch = adminPw2 !== "" && adminPw !== adminPw2;
  const adminOk =
    adminUser.trim() !== "" && adminPw.length >= MIN_PASSWORD && adminPw === adminPw2;

  const userPwShort = userPw !== "" && userPw.length < MIN_PASSWORD;
  const userPwMismatch = userPw2 !== "" && userPw !== userPw2;
  const userVmidOk = userVmid !== "" && userVmid >= MIN_VMID;
  const userOk =
    !wantUser ||
    (userName.trim() !== "" && userPw.length >= MIN_PASSWORD && userPw === userPw2 && userVmidOk);

  const runTest = async () => {
    if (testing || !connFilled) return;
    setTesting(true);
    setTestError(null);
    setTestResult(null);
    try {
      const res = await testSetupConnection(conn);
      setTestResult(res);
      setTestedFor(connSignature);
    } catch (err) {
      setTestedFor(null);
      setTestError(err instanceof Error ? err.message : "could not reach Proxmox");
    } finally {
      setTesting(false);
    }
  };

  const finish = async (e: FormEvent) => {
    e.preventDefault();
    if (saving || !tested || !adminOk || !userOk) return;
    setSaving(true);
    setSaveError(null);
    try {
      const s = await submitSetup({
        pve: conn,
        admin: { username: adminUser.trim(), password: adminPw },
        user: wantUser
          ? {
              username: userName.trim(),
              password: userPw,
              vmid: userVmid === "" ? MIN_VMID : userVmid,
            }
          : null,
      });
      // The backend already set the session cookie — we are signed in.
      onComplete(s);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setAlreadyDone(true);
      setSaveError(err instanceof Error ? err.message : "setup failed");
      setSaving(false);
    }
  };

  const next = (e: FormEvent) => {
    e.preventDefault();
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };
  const back = () => setStep((s) => Math.max(s - 1, 0));

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-xl">
        {/* Same hero as the login: this is the other door into the panel. */}
        <div className="reveal text-center mb-8" style={{ ["--step" as string]: 0 }}>
          <div className="eyebrow justify-center mb-4">First run</div>
          <Wordmark className="text-[44px] leading-none" />
          <p className="text-muted text-sm mt-4 max-w-sm mx-auto leading-relaxed">
            Nothing is configured yet. Point the seat at your Proxmox node, then claim it.
          </p>
        </div>

        <div className="reveal mb-4" style={{ ["--step" as string]: 1 }}>
          <StepRail step={step} />
        </div>

        {/* --- 1. Proxmox connection --- */}
        {step === 0 && (
          <form
            onSubmit={next}
            className="reveal card login-card p-5 sm:p-6 space-y-5"
            style={{ ["--step" as string]: 2 }}
          >
            <div className="eyebrow">Proxmox connection</div>

            <div className="grid gap-4 sm:grid-cols-[1fr_7rem]">
              <Field id="s-host" label="host">
                <input
                  id="s-host"
                  className="input metric"
                  value={host}
                  onChange={(e) => setHost(e.target.value.trim())}
                  placeholder="192.168.1.10"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  autoFocus
                  required
                />
              </Field>
              <Field id="s-port" label="port">
                <input
                  id="s-port"
                  type="number"
                  className="input metric"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(e) => setPort(e.target.value === "" ? "" : Number(e.target.value))}
                  required
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="s-node" label="node" hint="the Proxmox node this panel watches">
                <input
                  id="s-node"
                  className="input metric"
                  value={node}
                  onChange={(e) => setNode(e.target.value.trim())}
                  placeholder="pve"
                  spellCheck={false}
                  required
                />
              </Field>
              <Field
                id="s-scheme"
                label="protocol"
                hint="the Proxmox API speaks https on port 8006 — keep https. http exists for development mocks only."
              >
                <select
                  id="s-scheme"
                  className="input"
                  value={scheme}
                  onChange={(e) => setScheme(e.target.value as "https" | "http")}
                >
                  <option value="https">https (Proxmox default)</option>
                  <option value="http">http — dev mocks only</option>
                </select>
              </Field>
            </div>

            <div className="hairline" />

            <div className="eyebrow">API token</div>
            <Field
              id="s-token-id"
              label="token id"
              hint={
                <>
                  create it on the node:{" "}
                  <span className="metric">pveum user token add hlidskjalf@pve panel</span>
                </>
              }
            >
              <input
                id="s-token-id"
                className="input metric"
                value={tokenId}
                onChange={(e) => setTokenId(e.target.value.trim())}
                placeholder="hlidskjalf@pve!panel"
                autoComplete="off"
                spellCheck={false}
                required
              />
            </Field>
            <Field
              id="s-token-secret"
              label="token secret"
              hint="shown by Proxmox once, when the token is created"
            >
              <input
                id="s-token-secret"
                type="password"
                className="input"
                value={tokenSecret}
                onChange={(e) => setTokenSecret(e.target.value)}
                autoComplete="new-password"
                spellCheck={false}
                required
              />
            </Field>

            <Field
              id="s-fingerprint"
              label={scheme === "https" ? "certificate fingerprint" : "certificate fingerprint (n/a over http)"}
              error={
                fingerprint.trim() !== "" && !fingerprintOk
                  ? "expected a SHA-256 fingerprint — 64 hex characters, colons optional"
                  : null
              }
              hint={
                scheme === "https" ? (
                  <>
                    Proxmox certs are self-signed, so the panel pins this one exactly. Print it
                    on the node's shell:{" "}
                    <span className="metric text-fg break-all">
                      openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
                    </span>
                  </>
                ) : (
                  "http carries no certificate to pin"
                )
              }
            >
              <input
                id="s-fingerprint"
                className="input metric text-xs"
                value={fingerprint}
                onChange={(e) => setFingerprint(e.target.value.trim())}
                placeholder="AB:CD:… or 64 hex chars"
                autoComplete="off"
                spellCheck={false}
                disabled={scheme === "http"}
                required={scheme === "https"}
              />
            </Field>

            <div className="hairline" />

            {/* Nothing is committed until this proves the token reaches the node. */}
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="btn-cyan"
                onClick={() => void runTest()}
                disabled={!connFilled || testing}
              >
                {testing ? "reaching out…" : "test connection"}
              </button>
              {!connFilled && (
                <span className="text-muted text-xs">fill the fields above, then test</span>
              )}
            </div>

            {testResult && tested && (
              <div className="well p-3 text-sm" role="status">
                <div className="text-cyan">Proxmox answered.</div>
                <div className="text-muted text-xs mt-1">
                  node <span className="metric text-fg">{testResult.node}</span> ·{" "}
                  <span className="metric text-fg">{testResult.guests}</span>{" "}
                  {testResult.guests === 1 ? "guest" : "guests"}
                  {testResult.nodes.length > 1 && (
                    <>
                      {" "}
                      · cluster:{" "}
                      <span className="metric text-fg">{testResult.nodes.join(" ")}</span>
                    </>
                  )}
                </div>
              </div>
            )}
            {testResult && !tested && (
              <p className="text-amber text-xs" role="status">
                the connection changed since the last test — test again before continuing
              </p>
            )}
            {testError && (
              <div className="text-red text-sm" role="alert">
                {testError}
              </div>
            )}

            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted">
                {tested ? "connection verified" : "a successful test is required to continue"}
              </span>
              <button type="submit" className="btn-pink" disabled={!tested}>
                continue
              </button>
            </div>
          </form>
        )}

        {/* --- 2. Admin account --- */}
        {step === 1 && (
          <form
            onSubmit={next}
            className="reveal card login-card p-5 sm:p-6 space-y-5"
            style={{ ["--step" as string]: 2 }}
          >
            <div className="eyebrow">Admin account</div>
            <p className="text-muted text-sm">
              The operator of this panel — full sight of the fleet, and the only account that can
              create others.
            </p>

            <Field id="s-admin-user" label="username">
              <input
                id="s-admin-user"
                className="input"
                value={adminUser}
                onChange={(e) => setAdminUser(e.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                autoFocus
                required
              />
            </Field>
            <Field
              id="s-admin-pw"
              label="password"
              error={adminPwShort ? `at least ${MIN_PASSWORD} characters` : null}
              hint={`at least ${MIN_PASSWORD} characters`}
            >
              <input
                id="s-admin-pw"
                type="password"
                className="input"
                value={adminPw}
                onChange={(e) => setAdminPw(e.target.value)}
                autoComplete="new-password"
                minLength={MIN_PASSWORD}
                required
              />
            </Field>
            <Field
              id="s-admin-pw2"
              label="confirm password"
              error={adminPwMismatch ? "the two passwords do not match" : null}
            >
              <input
                id="s-admin-pw2"
                type="password"
                className="input"
                value={adminPw2}
                onChange={(e) => setAdminPw2(e.target.value)}
                autoComplete="new-password"
                required
              />
            </Field>

            <div className="flex items-center justify-between gap-3">
              <button type="button" className="btn-plain" onClick={back}>
                back
              </button>
              <button type="submit" className="btn-pink" disabled={!adminOk}>
                continue
              </button>
            </div>
          </form>
        )}

        {/* --- 3. First user (optional) --- */}
        {step === 2 && (
          <form
            onSubmit={next}
            className="reveal card login-card p-5 sm:p-6 space-y-5"
            style={{ ["--step" as string]: 2 }}
          >
            <div className="eyebrow">First user — optional</div>
            <p className="text-muted text-sm">
              A regular user sees one guest and nothing else. You can skip this and add users
              later from the Users page.
            </p>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={wantUser}
                onChange={(e) => setWantUser(e.target.checked)}
                className="accent-pink"
              />
              create a user now
            </label>

            {wantUser && (
              <div className="space-y-5 pt-1">
                <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
                  <Field id="s-user-name" label="username">
                    <input
                      id="s-user-name"
                      className="input"
                      value={userName}
                      onChange={(e) => setUserName(e.target.value)}
                      autoComplete="off"
                      autoCapitalize="none"
                      autoCorrect="off"
                      spellCheck={false}
                      required
                    />
                  </Field>
                  <Field
                    id="s-user-vmid"
                    label="their VM"
                    error={
                      userVmid !== "" && !userVmidOk ? `VMIDs start at ${MIN_VMID}` : null
                    }
                    hint="the one guest they can see"
                  >
                    {/* The connection test already told us what's on the node, so
                        pick from the real guests rather than typing a VMID from
                        memory. (Fall back to a number field if the list is empty.) */}
                    {testResult && testResult.guest_list?.length > 0 ? (
                      <select
                        id="s-user-vmid"
                        className="input metric"
                        value={userVmid}
                        onChange={(e) =>
                          setUserVmid(e.target.value === "" ? "" : Number(e.target.value))
                        }
                        required
                      >
                        <option value="">choose a guest…</option>
                        {testResult.guest_list.map((g) => (
                          <option key={g.vmid} value={g.vmid}>
                            {g.vmid} — {g.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id="s-user-vmid"
                        type="number"
                        className="input metric"
                        min={MIN_VMID}
                        value={userVmid}
                        onChange={(e) =>
                          setUserVmid(e.target.value === "" ? "" : Number(e.target.value))
                        }
                        placeholder="105"
                        required
                      />
                    )}
                  </Field>
                </div>
                <Field
                  id="s-user-pw"
                  label="password"
                  error={userPwShort ? `at least ${MIN_PASSWORD} characters` : null}
                  hint={`at least ${MIN_PASSWORD} characters`}
                >
                  <input
                    id="s-user-pw"
                    type="password"
                    className="input"
                    value={userPw}
                    onChange={(e) => setUserPw(e.target.value)}
                    autoComplete="new-password"
                    minLength={MIN_PASSWORD}
                    required
                  />
                </Field>
                <Field
                  id="s-user-pw2"
                  label="confirm password"
                  error={userPwMismatch ? "the two passwords do not match" : null}
                >
                  <input
                    id="s-user-pw2"
                    type="password"
                    className="input"
                    value={userPw2}
                    onChange={(e) => setUserPw2(e.target.value)}
                    autoComplete="new-password"
                    required
                  />
                </Field>
              </div>
            )}

            <div className="flex items-center justify-between gap-3">
              <button type="button" className="btn-plain" onClick={back}>
                back
              </button>
              <button type="submit" className="btn-pink" disabled={!userOk}>
                {wantUser ? "continue" : "skip this step"}
              </button>
            </div>
          </form>
        )}

        {/* --- 4. Review + finish --- */}
        {step === 3 && (
          <form
            onSubmit={finish}
            className="reveal card login-card p-5 sm:p-6 space-y-5"
            style={{ ["--step" as string]: 2 }}
          >
            <div className="eyebrow">Review</div>

            <div className="well p-3">
              <div className="text-[11px] uppercase tracking-eyebrow text-muted mb-1">Proxmox</div>
              <ReviewRow
                label="endpoint"
                value={`${conn.scheme}://${conn.host}:${conn.port}`}
              />
              <ReviewRow label="node" value={conn.node} />
              <ReviewRow label="token id" value={conn.token_id} />
              {/* The secret is never rendered back — it exists only in memory. */}
              <ReviewRow label="token secret" value="•••••••• (held, not shown)" />
              <ReviewRow
                label="fingerprint"
                value={conn.fingerprint === "" ? "none (http)" : conn.fingerprint}
              />
            </div>

            <div className="well p-3">
              <div className="text-[11px] uppercase tracking-eyebrow text-muted mb-1">Accounts</div>
              <ReviewRow label="admin" value={adminUser.trim()} />
              <ReviewRow
                label="first user"
                value={
                  wantUser ? `${userName.trim()} → vm ${userVmid}` : "none — add users later"
                }
              />
            </div>

            {!tested && (
              <p className="text-amber text-xs" role="status">
                the connection is no longer verified — go back and test it again
              </p>
            )}

            {saveError && <ErrorState message={saveError} />}
            {alreadyDone && (
              <p className="text-muted text-xs">
                Someone finished setup already.{" "}
                <a className="text-cyan" href="/login">
                  Go to sign-in →
                </a>
              </p>
            )}

            <div className="flex items-center justify-between gap-3">
              <button type="button" className="btn-plain" onClick={back} disabled={saving}>
                back
              </button>
              <button
                type="submit"
                className="btn-pink"
                disabled={saving || !tested || !adminOk || !userOk || alreadyDone}
              >
                {saving ? "taking the seat…" : "finish & take the seat"}
              </button>
            </div>
          </form>
        )}

        <p
          className="reveal text-center text-[10px] text-muted uppercase tracking-eyebrow mt-6"
          style={{ ["--step" as string]: 3 }}
        >
          Proxmox control panel
        </p>
      </div>
    </div>
  );
}
