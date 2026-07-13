import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { logout } from "../api";
import type { CurrentUser } from "../App";
import { ErrorBoundary } from "./ErrorBoundary";
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
      { to: "/debug", label: "Debug" },
    ];
  }
  // Regular user (VPS customer) — minimal nav focused on their VM
  return [
    { to: "/", label: "My VM", exact: true },
    { to: "/switch", label: "Switch" },
  ];
}

function navClass(isActive: boolean): string {
  return `block px-3 py-2 rounded-card text-sm border-l-2 transition-colors ${
    isActive
      ? "text-pink bg-pink/10 border-pink"
      : "text-muted border-transparent hover:text-fg hover:bg-border-token/30"
  }`;
}

export function Wordmark() {
  return (
    <span className="font-medium tracking-wide">
      <span className="text-pink">hlid</span>
      <span className="text-cyan">skjalf</span>
    </span>
  );
}

export function Layout({ currentUser, onLogout }: { currentUser: CurrentUser; onLogout?: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
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

  const userChip = (
    <div className="px-3 pt-3 mt-2 border-t border-border-token text-xs text-muted">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-fg truncate max-w-[7rem]" title={currentUser.username}>
          {currentUser.username}
        </span>
        <span
          className={`text-[10px] uppercase tracking-wider px-1 py-px rounded border ${
            currentUser.role === "admin"
              ? "text-pink border-pink/40 bg-pink/5"
              : "text-cyan border-cyan/30 bg-cyan/5"
          }`}
        >
          {currentUser.role}
        </span>
      </div>
      {currentUser.role === "user" && currentUser.vmid != null && (
        <div className="mt-0.5 metric">vm {currentUser.vmid}</div>
      )}
    </div>
  );

  return (
    <div className="min-h-screen md:flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-44 shrink-0 border-r border-border-token min-h-screen p-3 sticky top-0 h-screen bg-surface/30">
        <div className="px-3 py-2 mb-4">
          <Wordmark />
          <div className="text-[10px] text-muted tracking-widest mt-0.5">HIGH SEAT · HELLA</div>
        </div>
        <nav className="space-y-1 flex-1">{links()}</nav>
        {userChip}
        <button className="text-left px-3 py-2 text-sm text-muted hover:text-red transition-colors" onClick={doLogout}>
          logout
        </button>
      </aside>

      {/* Mobile top bar */}
      <header className="md:hidden flex items-center justify-between border-b border-border-token px-4 py-3 sticky top-0 bg-bg z-30">
        <Wordmark />
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
          {/* A crash on one page must not blank the panel; reset per navigation. */}
          <ErrorBoundary label="page" resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
