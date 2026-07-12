import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { restoreSession } from "./api";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { LoadingState } from "./components/ui";
import { Fleet } from "./pages/Fleet";
import { Login } from "./pages/Login";
import { NodePage } from "./pages/NodePage";
import { Provision } from "./pages/Provision";
import { SwitchPage } from "./pages/Switch";
import { VmDetailPage } from "./pages/VmDetail";

export function App() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    void restoreSession().then((s) => {
      setAuthed(s != null);
      setReady(true);
    });
  }, []);

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingState message="hlidskjalf…" />
      </div>
    );
  }

  return (
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login onLogin={() => setAuthed(true)} />} />
          {authed ? (
            <Route element={<Layout />}>
              <Route path="/" element={<Fleet />} />
              <Route path="/switch" element={<SwitchPage />} />
              <Route path="/vm/:vmid" element={<VmDetailPage />} />
              <Route path="/new" element={<Provision />} />
              <Route path="/node" element={<NodePage />} />
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
