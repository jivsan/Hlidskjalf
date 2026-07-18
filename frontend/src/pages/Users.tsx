import { useEffect, useState, type FormEvent } from "react";
import { api, getCurrentUsername } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";
import type { VmListItem } from "../types";

interface UserRow {
  id: number;
  username: string;
  role: string;
  vmid: number | null;
  email?: string;
  pangolin_state?: "" | "invited" | "active" | "error";
}

// What POST /api/users (+ the sync endpoint) returns about the edge identity.
interface PangolinSyncResult {
  state: "invited" | "active" | "error";
  inviteLink?: string;
  expiresAt?: number;
  error?: string;
}

// The one-time invite reveal: a bearer link, shown exactly once after create
// (or after a successful retry) and never stored anywhere.
interface InviteReveal {
  username: string;
  link: string;
  expiresAt?: number;
}

const USERNAME_RE = /^[a-z0-9]([a-z0-9._-]{0,30}[a-z0-9])?$/;
const MIN_PASSWORD_LEN = 8;

// Pangolin reports expiresAt as an epoch; the unit differs across versions —
// anything past 2100 in seconds is implausible, so small numbers are seconds.
const formatExpiry = (ts: number) => new Date(ts < 1e12 ? ts * 1000 : ts).toLocaleString();

export function UsersPage() {
  const toast = useToast();
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [vms, setVms] = useState<VmListItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [newUser, setNewUser] = useState({ username: "", password: "", role: "user", vmid: "", email: "" });
  const [creating, setCreating] = useState(false);
  const [invite, setInvite] = useState<InviteReveal | null>(null);

  // Per-row modal state
  const [assignFor, setAssignFor] = useState<UserRow | null>(null);
  const [assignVmid, setAssignVmid] = useState<string>("");
  const [pwFor, setPwFor] = useState<UserRow | null>(null);
  const [pwDraft, setPwDraft] = useState("");
  // Changing your OWN password requires proving you know the current one (an
  // admin resetting someone else's does not).
  const [pwCurrent, setPwCurrent] = useState("");
  const [deleteFor, setDeleteFor] = useState<UserRow | null>(null);
  const [rowBusy, setRowBusy] = useState(false);

  async function load() {
    setLoadError(null);
    try {
      const [u, v] = await Promise.all([
        api.get<UserRow[]>("/api/users"),
        api.get<VmListItem[]>("/api/vms"),
      ]);
      setUsers(u);
      setVms(v);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "failed to load users");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const usernameOk = USERNAME_RE.test(newUser.username);
  const passwordOk = newUser.password.length >= MIN_PASSWORD_LEN;
  const createOk = usernameOk && passwordOk;

  async function createUser(e: FormEvent) {
    e.preventDefault();
    if (!createOk) return;
    setCreating(true);
    setInvite(null);
    try {
      const resp = await api.post<{ pangolin?: PangolinSyncResult }>("/api/users", {
        username: newUser.username,
        password: newUser.password,
        role: newUser.role,
        vmid: newUser.vmid ? Number(newUser.vmid) : null,
        ...(newUser.email.trim() ? { email: newUser.email.trim() } : {}),
      });
      toast.success(`user ${newUser.username} created`);
      const pg = resp.pangolin;
      if (pg?.state === "invited" && pg.inviteLink) {
        setInvite({ username: newUser.username, link: pg.inviteLink, expiresAt: pg.expiresAt });
      } else if (pg?.state === "error") {
        toast.error(`pangolin invite failed (user still created): ${pg.error ?? "unknown"}`);
      }
      setNewUser({ username: "", password: "", role: "user", vmid: "", email: "" });
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "create failed");
    } finally {
      setCreating(false);
    }
  }

  async function retrySync(u: UserRow) {
    try {
      const pg = await api.post<PangolinSyncResult>(
        `/api/users/${encodeURIComponent(u.username)}/pangolin-sync`,
        {},
      );
      if (pg.state === "invited" && pg.inviteLink) {
        setInvite({ username: u.username, link: pg.inviteLink, expiresAt: pg.expiresAt });
      } else {
        toast.success(`${u.username}: edge identity ${pg.state}`);
      }
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "sync failed");
    }
  }

  async function saveAssign() {
    if (!assignFor) return;
    setRowBusy(true);
    try {
      const vmid = assignVmid === "" ? null : Number(assignVmid);
      await api.post(`/api/users/${encodeURIComponent(assignFor.username)}/assign`, { vmid });
      toast.success(
        vmid == null
          ? `${assignFor.username}: VM unassigned`
          : `${assignFor.username} → vm ${vmid}`,
      );
      setAssignFor(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "assign failed");
    } finally {
      setRowBusy(false);
    }
  }

  async function savePassword() {
    if (!pwFor || pwDraft.length < MIN_PASSWORD_LEN) return;
    const self = pwFor.username === getCurrentUsername();
    if (self && !pwCurrent) return;
    setRowBusy(true);
    try {
      await api.post(`/api/users/${encodeURIComponent(pwFor.username)}/password`, {
        password: pwDraft,
        ...(self ? { current_password: pwCurrent } : {}),
      });
      toast.success(
        self
          ? "password changed — your other sessions were signed out"
          : `password reset for ${pwFor.username} — their sessions were signed out`,
      );
      setPwFor(null);
      setPwDraft("");
      setPwCurrent("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "password update failed");
    } finally {
      setRowBusy(false);
    }
  }

  async function deleteUser() {
    if (!deleteFor) return;
    setRowBusy(true);
    try {
      await api.del(`/api/users/${encodeURIComponent(deleteFor.username)}`);
      toast.success(`user ${deleteFor.username} deleted`);
      setDeleteFor(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "delete failed");
    } finally {
      setRowBusy(false);
    }
  }

  if (loading) return <LoadingState />;
  if (loadError && !users) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="access control" title="Users" />
        <ErrorState message={loadError} />
        <button className="btn-plain" onClick={() => { setLoading(true); void load(); }}>
          retry
        </button>
      </div>
    );
  }

  const list = users ?? [];
  const vmList = vms ?? [];
  const assignedVmids = new Set(list.map((u) => u.vmid).filter((v): v is number => v != null));
  const vmName = (vmid: number | null) =>
    vmid == null ? null : (vmList.find((v) => v.vmid === vmid)?.name ?? null);

  // VM options for a select: free VMs + (optionally) the one currently held by `keep`.
  const vmOptions = (keep?: number | null) =>
    vmList.filter((v) => !assignedVmids.has(v.vmid) || v.vmid === keep);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="access control"
        title="Users"
        actions={
          <div className="text-xs text-muted">
            <span className="metric text-fg">{list.length}</span> users ·{" "}
            <span className="metric text-fg">{assignedVmids.size}</span> VMs assigned
          </div>
        }
      />

      {loadError && <ErrorState message={loadError} />}

      <Card title="Create user" className="card-brackets-hover">
        <form onSubmit={createUser} className="grid grid-cols-1 md:grid-cols-6 gap-3">
          <input
            className="input"
            placeholder="username"
            value={newUser.username}
            onChange={(e) => setNewUser({ ...newUser, username: e.target.value.toLowerCase().trim() })}
            autoComplete="off"
            spellCheck={false}
            required
          />
          <input
            className="input"
            type="password"
            placeholder={`password (min ${MIN_PASSWORD_LEN})`}
            value={newUser.password}
            onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
            autoComplete="new-password"
            required
          />
          <input
            className="input"
            type="email"
            placeholder="email (optional — SSO invite)"
            value={newUser.email}
            onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
            autoComplete="off"
            spellCheck={false}
            aria-label="email for Pangolin SSO invite"
          />
          <select
            className="input"
            value={newUser.role}
            onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
            aria-label="role"
          >
            <option value="user">user (one VM)</option>
            <option value="admin">admin (full access)</option>
          </select>
          <select
            className="input"
            value={newUser.vmid}
            onChange={(e) => setNewUser({ ...newUser, vmid: e.target.value })}
            aria-label="assigned VM"
            disabled={newUser.role === "admin"}
          >
            <option value="">no VM assigned</option>
            {vmOptions().map((v) => (
              <option key={v.vmid} value={v.vmid}>
                {v.vmid} — {v.name}
              </option>
            ))}
          </select>
          <button type="submit" className="btn-pink" disabled={!createOk || creating}>
            {creating ? "creating…" : "create user"}
          </button>
        </form>
        <div className="text-xs text-muted mt-2 space-y-0.5">
          {newUser.username && !usernameOk && (
            <p className="text-red">username: lowercase letters/digits (dots, dashes, underscores inside)</p>
          )}
          {newUser.password && !passwordOk && (
            <p className="text-red">password must be at least {MIN_PASSWORD_LEN} characters</p>
          )}
          <p>Regular users see only their assigned VM (power, graphs, console, bandwidth, rescue). Admins manage everything.</p>
          <p>With an email + Pangolin sync enabled, the tenant is also invited past the panel's SSO wall — the invite link is shown once.</p>
        </div>
      </Card>

      {/* One-time invite reveal — the link is a bearer secret: shown here
          exactly once, never stored, never sent by the panel. */}
      {invite && (
        <Card title={`Edge invite — ${invite.username}`} className="border-cyan/40">
          <div className="space-y-2 text-sm">
            <p className="text-muted text-xs">
              Send this link to {invite.username} out-of-band (Signal, in person — not
              email if you can avoid it). It is shown <span className="text-fg">once</span>
              {invite.expiresAt ? <> and expires {formatExpiry(invite.expiresAt)}</> : null}.
              They set their own Pangolin password — then should add a passkey in Pangolin:
              that login is phishing-proof.
            </p>
            <div className="flex gap-2">
              <input className="input metric text-xs flex-1" readOnly value={invite.link} onFocus={(e) => e.target.select()} aria-label="invite link" />
              <button
                className="btn-cyan text-xs px-3"
                onClick={() => {
                  void navigator.clipboard?.writeText(invite.link).then(
                    () => toast.success("invite link copied"),
                    () => toast.error("copy failed — select and copy manually"),
                  );
                }}
              >
                copy
              </button>
              <button className="btn-plain text-xs px-3" onClick={() => setInvite(null)}>
                done
              </button>
            </div>
          </div>
        </Card>
      )}

      {list.length === 0 ? (
        <EmptyState message="no users yet" />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] font-medium text-muted uppercase tracking-eyebrow border-b border-border-token">
              <tr>
                <th className="px-3 py-2.5 font-medium">Username</th>
                <th className="px-3 py-2.5 font-medium">Role</th>
                <th className="px-3 py-2.5 font-medium">Edge (SSO)</th>
                <th className="px-3 py-2.5 font-medium">Assigned VM</th>
                <th className="px-3 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((u) => (
                <tr key={u.id} className="border-b border-border-token/50 last:border-0">
                  <td className="px-3 py-2.5 font-mono text-fg">{u.username}</td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border ${
                        u.role === "admin"
                          ? "text-pink border-pink/40 bg-pink/5"
                          : "text-cyan border-cyan/30 bg-cyan/5"
                      }`}
                    >
                      {u.role}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    {!u.email ? (
                      <span className="text-muted text-xs">—</span>
                    ) : u.pangolin_state === "invited" ? (
                      <span
                        className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border text-cyan border-cyan/30 bg-cyan/5"
                        title={`invite sent to ${u.email} — not accepted yet`}
                      >
                        invited
                      </span>
                    ) : u.pangolin_state === "active" ? (
                      <span
                        className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border text-green border-green/30 bg-green/5"
                        title={`edge identity live for ${u.email}`}
                      >
                        active
                      </span>
                    ) : u.pangolin_state === "error" ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border text-red border-red/40 bg-red/5"
                          title="the last invite attempt failed — retry"
                        >
                          error
                        </span>
                        <button className="btn-plain px-1.5 py-0.5 text-[10px]" onClick={() => void retrySync(u)}>
                          retry
                        </button>
                      </span>
                    ) : (
                      <span className="text-muted text-xs" title={`${u.email} — no edge identity (sync off or not yet invited)`}>
                        no edge
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 metric">
                    {u.vmid != null ? (
                      <>
                        {u.vmid}
                        {vmName(u.vmid) && <span className="text-muted"> — {vmName(u.vmid)}</span>}
                      </>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right whitespace-nowrap space-x-1">
                    {u.role !== "admin" && (
                      <button
                        className="btn-plain px-2 py-0.5 text-xs"
                        onClick={() => {
                          setAssignFor(u);
                          setAssignVmid(u.vmid != null ? String(u.vmid) : "");
                        }}
                      >
                        assign VM
                      </button>
                    )}
                    <button
                      className="btn-plain px-2 py-0.5 text-xs"
                      onClick={() => {
                        setPwFor(u);
                        setPwDraft("");
                      }}
                    >
                      reset pw
                    </button>
                    <button
                      className="btn-red px-2 py-0.5 text-xs"
                      onClick={() => setDeleteFor(u)}
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Assign VM modal */}
      <ConfirmDialog
        open={assignFor != null}
        title={`Assign VM — ${assignFor?.username ?? ""}`}
        confirmLabel="save"
        confirmClass="btn-cyan"
        busy={rowBusy}
        onConfirm={() => void saveAssign()}
        onCancel={() => setAssignFor(null)}
      >
        <p>Each regular user is tied to exactly one VM.</p>
        <select
          className="input"
          value={assignVmid}
          onChange={(e) => setAssignVmid(e.target.value)}
          aria-label="VM to assign"
        >
          <option value="">no VM (unassign)</option>
          {vmOptions(assignFor?.vmid).map((v) => (
            <option key={v.vmid} value={v.vmid}>
              {v.vmid} — {v.name}
            </option>
          ))}
        </select>
      </ConfirmDialog>

      {/* Reset password modal */}
      <ConfirmDialog
        open={pwFor != null}
        title={
          pwFor?.username === getCurrentUsername()
            ? "Change your password"
            : `Reset password — ${pwFor?.username ?? ""}`
        }
        confirmLabel="set password"
        confirmClass="btn-cyan"
        busy={rowBusy}
        onConfirm={() => void savePassword()}
        onCancel={() => {
          setPwFor(null);
          setPwDraft("");
          setPwCurrent("");
        }}
      >
        {pwFor?.username === getCurrentUsername() ? (
          <>
            <p>
              Confirm your current password to change it. Every other session signed in as
              you will be signed out.
            </p>
            <input
              className="input"
              type="password"
              placeholder="current password"
              value={pwCurrent}
              onChange={(e) => setPwCurrent(e.target.value)}
              autoComplete="current-password"
              autoFocus
            />
          </>
        ) : (
          <p>
            The user will need this new password on their next login. Their existing
            sessions will be signed out.
          </p>
        )}
        <input
          className="input"
          type="password"
          placeholder={`new password (min ${MIN_PASSWORD_LEN})`}
          value={pwDraft}
          onChange={(e) => setPwDraft(e.target.value)}
          autoComplete="new-password"
          autoFocus={pwFor?.username !== getCurrentUsername()}
        />
        {pwDraft.length > 0 && pwDraft.length < MIN_PASSWORD_LEN && (
          <p className="text-red text-xs">at least {MIN_PASSWORD_LEN} characters</p>
        )}
      </ConfirmDialog>

      {/* Delete user confirm */}
      <ConfirmDialog
        open={deleteFor != null}
        title={`Delete user — ${deleteFor?.username ?? ""}`}
        confirmLabel="delete user"
        confirmClass="btn-red"
        requireText={deleteFor?.username}
        busy={rowBusy}
        onConfirm={() => void deleteUser()}
        onCancel={() => setDeleteFor(null)}
      >
        <p>
          <span className="text-red">{deleteFor?.username}</span> loses access immediately.
          Their VM is not touched — it just becomes unassigned.
          {deleteFor?.email ? " Their Pangolin edge identity is removed too (best-effort)." : ""}{" "}
          The last admin cannot be deleted.
        </p>
      </ConfirmDialog>
    </div>
  );
}
