import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { getNodeName, logout } from "../api";
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
  // Active links carry a left aurora bar + a raised surface; the seat marks
  // where you're looking. Inactive links stay quiet.
  return [
    "group relative flex items-center rounded-card px-3 py-2 text-sm transition-colors",
    "before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-4 before:w-0.5 before:rounded-full before:transition-all",
    isActive
      ? "text-fg bg-surface-2 before:bg-cyan"
      : "text-muted hover:text-fg hover:bg-surface/60 before:bg-transparent",
  ].join(" ");
}

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`wordmark ${className}`}>
      <span className="text-pink">hlid</span>
      <span className="text-cyan">skjalf</span>
    </span>
  );
}

function RoleBadge({ role }: { role: string }) {
  return (
    <span
      className={`text-[10px] font-medium uppercase tracking-wider px-1.5 py-0.5 rounded border ${
        role === "admin"
          ? "text-pink border-pink/40 bg-pink/5"
          : "text-cyan border-cyan/30 bg-cyan/5"
      }`}
    >
      {role}
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

  return (
    <div className="min-h-screen md:flex">
      {/* Desktop rail — the high seat's instrument panel */}
      <aside className="hidden md:flex flex-col w-52 shrink-0 border-r border-border-token min-h-screen p-3 sticky top-0 h-screen bg-surface/25">
        {/* Identity */}
        <div className="reveal px-2 pt-2 pb-4" style={{ ["--step" as string]: 0 }}>
          <Wordmark className="text-[22px]" />
          <div className="hairline my-3" />
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">high seat</span>
            {/* The node comes from the session — every deployment shows its own. */}
            <span
              className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted min-w-0"
              title={getNodeName()}
            >
              <span className="relative inline-flex shrink-0">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan" />
                <span className="absolute inset-0 rounded-full bg-cyan/60 animate-ping" aria-hidden="true" />
              </span>
              <span className="truncate">{getNodeName()}</span>
            </span>
          </div>
        </div>

        <nav className="reveal space-y-0.5 flex-1" style={{ ["--step" as string]: 1 }}>
          {links()}
        </nav>

        {/* Watcher */}
        <div className="reveal pt-3 mt-2 border-t border-border-token" style={{ ["--step" as string]: 2 }}>
          <div className="flex items-center gap-2 px-2 mb-2">
            <Link
              to="/profile"
              className="text-sm text-fg truncate hover:text-cyan transition-colors"
              title="your account"
            >
              {currentUser.username}
            </Link>
            <RoleBadge role={currentUser.role} />
          </div>
          {currentUser.role === "user" && currentUser.vmid != null && (
            <div className="px-2 mb-2 text-[11px] text-muted metric">vm {currentUser.vmid}</div>
          )}
          <button
            className="w-full text-left px-2 py-1.5 text-sm text-muted hover:text-red transition-colors"
            onClick={doLogout}
          >
            leave the seat
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="md:hidden flex items-center justify-between border-b border-border-token px-4 py-3 sticky top-0 bg-bg z-30">
        <Wordmark className="text-lg" />
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
        <nav className="md:hidden border-b border-border-token px-4 py-2 space-y-0.5 bg-bg sticky top-12 z-30">
          {links(() => setMenuOpen(false))}
          <button
            className="block w-full text-left px-3 py-2 text-sm text-muted hover:text-red"
            onClick={doLogout}
          >
            leave the seat
          </button>
          <div className="px-3 py-1 flex items-center gap-2 text-[11px] text-muted">
            <Link
              to="/profile"
              className="hover:text-fg transition-colors"
              title="your account"
              onClick={() => setMenuOpen(false)}
            >
              {currentUser.username}
            </Link>{" "}
            <RoleBadge role={currentUser.role} />
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
