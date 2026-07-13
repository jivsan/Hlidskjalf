import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { getSetupStatus, restoreSession, type SessionInfo } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { LoadingState } from "./components/ui";
import { Login } from "./pages/Login";

// Everything behind the auth wall is code-split per route: the entry chunk only
// carries the shell (router + Layout + Login). Recharts in particular is heavy
// and only reachable from NodePage / the VM graphs + overview tabs, so it lands
// in its own async chunk instead of first paint.
const Fleet = lazy(() => import("./pages/Fleet").then((m) => ({ default: m.Fleet })));
const NodePage = lazy(() => import("./pages/NodePage").then((m) => ({ default: m.NodePage })));
const Provision = lazy(() => import("./pages/Provision").then((m) => ({ default: m.Provision })));
const SwitchPage = lazy(() => import("./pages/Switch").then((m) => ({ default: m.SwitchPage })));
const UsersPage = lazy(() => import("./pages/Users").then((m) => ({ default: m.UsersPage })));
const VmDetailPage = lazy(() =>
  import("./pages/VmDetail").then((m) => ({ default: m.VmDetailPage })),
);
const Debug = lazy(() => import("./pages/Debug").then((m) => ({ default: m.Debug })));
const Profile = lazy(() => import("./pages/Profile").then((m) => ({ default: m.Profile })));
// The wizard is reachable exactly once in a deployment's life — never make the
// other 99.99% of loads pay for it.
const Setup = lazy(() => import("./pages/Setup").then((m) => ({ default: m.Setup })));

// Suspense sits *inside* the Layout outlet, so the chrome (nav/header) never
// unmounts while a route chunk loads — the page body shows the same
// <LoadingState/> these pages already show while their first poll is in flight.
function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<LoadingState />}>{children}</Suspense>;
}

export interface CurrentUser {
  username: string;
  role: "admin" | "user";
  vmid: number | null;
}

export function App() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [setupNeeded, setSetupNeeded] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    // Setup status comes first: on a fresh deployment there is no session to
    // restore and no login to offer — only the wizard. If the status call itself
    // fails (old backend, proxy hiccup) we assume a configured panel and fall
    // through to the normal session path rather than trap the operator here.
    void (async () => {
      let needed = false;
      try {
        needed = (await getSetupStatus()).needed;
      } catch {
        needed = false;
      }
      setSetupNeeded(needed);
      if (!needed) {
        const s: SessionInfo | null = await restoreSession();
        if (s) {
          const role = (s.role === "admin" ? "admin" : "user") as "admin" | "user";
          setCurrentUser({ username: s.user, role, vmid: s.vmid ?? null });
          setAuthed(true);
        } else {
          setAuthed(false);
        }
      }
      setReady(true);
    })();
  }, []);

  const handleLogin = (s: SessionInfo) => {
    const role = (s.role === "admin" ? "admin" : "user") as "admin" | "user";
    setCurrentUser({ username: s.user, role, vmid: s.vmid ?? null });
    setAuthed(true);
  };

  // Finishing the wizard signs the admin in (the backend set the cookie), so the
  // setup gate drops and the panel routes come up in the same render.
  const handleSetupComplete = (s: SessionInfo) => {
    setSetupNeeded(false);
    handleLogin(s);
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
    <ErrorBoundary label="panel">
      <ToastProvider>
        <BrowserRouter>
        <Routes>
          {/* Unconfigured panel: the wizard swallows every route — there is no
              login to offer and nothing behind it to protect yet. */}
          {setupNeeded ? (
            <>
              <Route
                path="/"
                element={
                  <Suspense
                    fallback={
                      <div className="min-h-screen flex items-center justify-center">
                        <LoadingState message="hlidskjalf…" />
                      </div>
                    }
                  >
                    <Setup onComplete={handleSetupComplete} />
                  </Suspense>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </>
          ) : (
            <>
          <Route path="/login" element={<Login onLogin={handleLogin} />} />
          {authed && currentUser ? (
            <Route element={<Layout currentUser={currentUser} onLogout={() => { setAuthed(false); setCurrentUser(null); }} />}>
              <Route path="/switch" element={<Page><SwitchPage /></Page>} />
              <Route path="/profile" element={<Page><Profile currentUser={currentUser} /></Page>} />
              <Route path="/vm/:vmid" element={<Page><VmDetailPage currentRole={currentUser.role} myVmid={currentUser.vmid} /></Page>} />
              {/* Admin-only sections */}
              {isAdmin && <Route path="/new" element={<Page><Provision /></Page>} />}
              {isAdmin && <Route path="/node" element={<Page><NodePage /></Page>} />}
              {isAdmin && <Route path="/users" element={<Page><UsersPage /></Page>} />}
              {isAdmin && <Route path="/debug" element={<Page><Debug /></Page>} />}
              {/* Home: users go to their VM, admins go to fleet */}
              <Route path="/" element={
                isAdmin
                  ? <Page><Fleet /></Page>
                  : (currentUser.vmid ? <Navigate to={`/vm/${currentUser.vmid}`} replace /> : <div className="p-6">No VM assigned. Contact admin.</div>)
              } />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          ) : (
            <Route path="*" element={<Navigate to="/login" replace />} />
          )}
            </>
          )}
        </Routes>
        </BrowserRouter>
      </ToastProvider>
    </ErrorBoundary>
  );
}
