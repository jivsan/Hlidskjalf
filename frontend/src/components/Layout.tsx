import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { logout } from "../api";
import type { CurrentUser } from "../App";
import { useToast } from "./Toast";

function getNavForRole(user: CurrentUser | null) {
  if (!user) return [];
  if (user.role === "admin") {
    return [
      { to: "/", label: "Fleet", exact: true },
      { to: "/switch", label: "Switch" },
      { to: "/new", label: "Provision" },
      { to: "/node", label: "Node" },
      { to: "/users", label: "Users" },
    ];
  }
  // Regular user (VPS customer) — minimal nav focused on their VM
  return [
    { to: "/", label: "My VM", exact: true },
    { to: "/switch", label: "Switch" },
  ];
}

function navClass(isActive: boolean): string {
  return `block px-3 py-2 rounded-card text-sm ${
    isActive ? "text-pink bg-pink/10" : "text-muted hover:text-fg"
  }`;
}

export function Layout({ currentUser, onLogout }: { currentUser: CurrentUser; onLogout?: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [menuOpen, setMenuOpen] = useState(false);

  const NAV = getNavForRole(currentUser);

  const doLogout = async () => {
    try {
      await logout();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "logout failed");
    }
    if (onLogout) onLogout();
    navigate("/login");
  };

  const links = (onNav?: () => void) =>
    NAV.map((n) => (
      <NavLink
        key={n.to}
        to={n.to}
        end={n.exact}
        className={({ isActive }) => navClass(isActive)}
        onClick={onNav}
      >
        {n.label}
      </NavLink>
    ));

  return (
    <div className="min-h-screen md:flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-44 shrink-0 border-r border-border-token min-h-screen p-3 sticky top-0 h-screen">
        <div className="px-3 py-2 mb-4">
          <span className="text-pink">hlid</span>
          <span className="text-cyan">skjalf</span>
        </div>
        <nav className="space-y-1 flex-1">{links()}</nav>
        <div className="px-3 pt-3 mt-2 border-t border-border-token text-xs text-muted">
          {currentUser.username}
          {currentUser.role === "user" && currentUser.vmid ? ` (vm ${currentUser.vmid})` : ""}
          <span className="ml-1 text-[10px] px-1 py-0.5 rounded bg-white/5">{currentUser.role}</span>
        </div>
        <button className="text-left px-3 py-2 text-sm text-muted hover:text-red" onClick={doLogout}>
          logout
        </button>
      </aside>

      {/* Mobile top bar */}
      <header className="md:hidden flex items-center justify-between border-b border-border-token px-4 py-3 sticky top-0 bg-bg z-30">
        <div>
          <span className="text-pink">hlid</span>
          <span className="text-cyan">skjalf</span>
        </div>
        <button
          className="btn-plain px-2 py-1"
          aria-label="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          ≡
        </button>
      </header>
      {menuOpen && (
        <nav className="md:hidden border-b border-border-token px-4 py-2 space-y-1 bg-bg sticky top-12 z-30">
          {links(() => setMenuOpen(false))}
          <button
            className="block w-full text-left px-3 py-2 text-sm text-muted hover:text-red"
            onClick={doLogout}
          >
            logout
          </button>
          <div className="px-3 py-1 text-[10px] text-muted">
            {currentUser.username} · {currentUser.role}
          </div>
        </nav>
      )}

      <main className="flex-1 min-w-0">
        <div className="max-w-6xl mx-auto p-4 md:p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
