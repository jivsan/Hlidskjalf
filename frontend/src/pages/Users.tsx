import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../components/Toast";
import { Card, LoadingState } from "../components/ui";

interface UserRow {
  id: number;
  username: string;
  role: string;
  vmid: number | null;
}

export function UsersPage() {
  const toast = useToast();
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [vms, setVms] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(true);

  const [newUser, setNewUser] = useState({ username: "", password: "", vmid: "" as string | number });

  async function load() {
    setLoading(true);
    try {
      const [u, v] = await Promise.all([
        api.get<UserRow[]>("/api/users"),
        api.get<any[]>("/api/vms"),
      ]);
      setUsers(u);
      setVms(v);
    } catch (e) {
      toast.error("Failed to load users");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function createUser(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/users", {
        username: newUser.username,
        password: newUser.password,
        role: "user",
        vmid: newUser.vmid ? Number(newUser.vmid) : null,
      });
      toast.success("User created");
      setNewUser({ username: "", password: "", vmid: "" });
      await load();
    } catch (err: any) {
      toast.error(err.message || "Create failed");
    }
  }

  if (loading) return <LoadingState />;

  const assignedVmids = new Set((users || []).map(u => u.vmid).filter(Boolean));

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Users</h1>

      <Card className="p-4">
        <form onSubmit={createUser} className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <input className="input" placeholder="username" value={newUser.username} onChange={e => setNewUser({ ...newUser, username: e.target.value })} required />
          <input className="input" type="password" placeholder="temp password" value={newUser.password} onChange={e => setNewUser({ ...newUser, password: e.target.value })} required />
          <select className="input" value={newUser.vmid} onChange={e => setNewUser({ ...newUser, vmid: e.target.value })}>
            <option value="">No VM assigned</option>
            {vms?.map(v => (
              <option key={v.vmid} value={v.vmid} disabled={assignedVmids.has(v.vmid)}>
                {v.vmid} — {v.name}
              </option>
            ))}
          </select>
          <button type="submit" className="btn-pink">Create user</button>
        </form>
        <p className="text-xs text-muted mt-2">Regular users can only access their assigned VM (power, graphs, console, bandwidth). Admins have full access.</p>
      </Card>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-left text-muted border-b border-border-token">
            <tr>
              <th className="p-3">Username</th>
              <th className="p-3">Role</th>
              <th className="p-3">Assigned VM</th>
              <th className="p-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users?.map(u => (
              <tr key={u.id} className="border-b border-border-token last:border-none">
                <td className="p-3 font-mono">{u.username}</td>
                <td className="p-3"><span className={u.role === "admin" ? "text-pink" : ""}>{u.role}</span></td>
                <td className="p-3">{u.vmid ?? <span className="text-muted">—</span>}</td>
                <td className="p-3 text-xs space-x-2">
                  <button className="text-cyan hover:underline" onClick={async () => {
                    const v = prompt("Assign VMID (empty to unassign)", u.vmid?.toString() || "");
                    const vmid = v === "" || v === null ? null : Number(v);
                    await api.post(`/api/users/${u.username}/assign`, { vmid });
                    await load();
                  }}>assign</button>
                  <button className="text-cyan hover:underline" onClick={async () => {
                    const pw = prompt(`New password for ${u.username}`);
                    if (pw) {
                      await api.post(`/api/users/${u.username}/password`, { password: pw });
                      toast.success("Password updated");
                    }
                  }}>reset pw</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
