import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { restoreSession, type SessionInfo } from "./api";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { LoadingState } from "./components/ui";
import { Fleet } from "./pages/Fleet";
import { Login } from "./pages/Login";
import { NodePage } from "./pages/NodePage";
import { Provision } from "./pages/Provision";
import { SwitchPage } from "./pages/Switch";
import { UsersPage } from "./pages/Users";
import { VmDetailPage } from "./pages/VmDetail";
import { Debug } from "./pages/Debug";

export interface CurrentUser {
  username: string;
  role: "admin" | "user";
  vmid: number | null;
}

export function App() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    void restoreSession().then((s: SessionInfo | null) => {
      if (s) {
        const role = (s.role === "admin" ? "admin" : "user") as "admin" | "user";
        setCurrentUser({ username: s.user, role, vmid: s.vmid ?? null });
        setAuthed(true);
      } else {
        setAuthed(false);
      }
      setReady(true);
    });
  }, []);

  const handleLogin = (s: SessionInfo) => {
    const role = (s.role === "admin" ? "admin" : "user") as "admin" | "user";
    setCurrentUser({ username: s.user, role, vmid: s.vmid ?? null });
    setAuthed(true);
  };

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingState message="hlidskjalf…" />
      </div>
    );
  }

  const isAdmin = currentUser?.role === "admin";

  return (
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login onLogin={handleLogin} />} />
          {authed && currentUser ? (
            <Route element={<Layout currentUser={currentUser} onLogout={() => { setAuthed(false); setCurrentUser(null); }} />}>
              <Route path="/switch" element={<SwitchPage />} />
              <Route path="/vm/:vmid" element={<VmDetailPage currentRole={currentUser.role} myVmid={currentUser.vmid} />} />
              {/* Admin-only sections */}
              {isAdmin && <Route path="/new" element={<Provision />} />}
              {isAdmin && <Route path="/node" element={<NodePage />} />}
              {isAdmin && <Route path="/users" element={<UsersPage />} />}
              {isAdmin && <Route path="/debug" element={<Debug />} />}
              {/* Home: users go to their VM, admins go to fleet */}
              <Route path="/" element={
                isAdmin
                  ? <Fleet />
                  : (currentUser.vmid ? <Navigate to={`/vm/${currentUser.vmid}`} replace /> : <div className="p-6">No VM assigned. Contact admin.</div>)
              } />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          ) : (
            <Route path="*" element={<Navigate to="/login" replace />} />
          )}
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}
