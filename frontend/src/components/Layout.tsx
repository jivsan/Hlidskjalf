import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { logout } from "../api";
import { useToast } from "./Toast";

const NAV = [
  { to: "/", label: "Fleet", exact: true },
  { to: "/new", label: "Provision" },
  { to: "/node", label: "Node" },
];

function navClass(isActive: boolean): string {
  return `block px-3 py-2 rounded-card text-sm ${
    isActive ? "text-pink bg-pink/10" : "text-muted hover:text-fg"
  }`;
}

export function Layout() {
  const navigate = useNavigate();
  const toast = useToast();
  const [menuOpen, setMenuOpen] = useState(false);

  const doLogout = async () => {
    try {
      await logout();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "logout failed");
    }
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
