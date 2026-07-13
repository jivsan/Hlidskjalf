// Tiny typed fetch wrapper. Keeps the CSRF token in module state, attaches
// X-Hlidskjalf-CSRF on mutating requests, and redirects to /login on any 401.

let csrfToken: string | null = null;

export function setCsrf(token: string | null) {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function redirectToLogin() {
  setCsrf(null);
  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

// Requests that exceed this are aborted so a hung backend/proxy can't wedge
// spinners forever. Long-running work (provision, power) is tracked via task
// UPIDs, so no legitimate API call should take anywhere near this.
const REQUEST_TIMEOUT_MS = 20_000;

async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
  opts?: { skipAuthRedirect?: boolean },
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-Hlidskjalf-CSRF"] = csrfToken;

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, "request timed out — backend unreachable?");
    }
    throw new ApiError(0, "network error — backend unreachable?");
  } finally {
    window.clearTimeout(timeout);
  }

  if (res.status === 401 && !opts?.skipAuthRedirect) {
    redirectToLogin();
    throw new ApiError(401, "Not authenticated");
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
      else if (data?.detail) detail = JSON.stringify(data.detail);
      else if (typeof data?.message === "string") detail = data.message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  try {
    return (await res.json()) as T;
  } catch {
    // 204s / empty bodies — callers treat this as "no payload".
    return undefined as T;
  }
}

export const api = {
  get: <T>(path: string, opts?: { skipAuthRedirect?: boolean }) =>
    request<T>("GET", path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: { skipAuthRedirect?: boolean }) =>
    request<T>("POST", path, body ?? {}, opts),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  del: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

// --- Session helpers ---

export interface SessionInfo {
  user: string;
  role?: "admin" | "user";
  vmid?: number | null;
  csrf: string;
  /** The Proxmox node this panel watches. Rendered instead of a hardcoded host. */
  node?: string;
}

// Session-scoped facts the whole UI reads. Populated before the app renders (the
// session is restored first), so plain getters are enough — no context needed.
let nodeName = "";
let currentUsername = "";

/** The Proxmox node name, e.g. "pve". Empty until the session is known. */
export function getNodeName(): string {
  return nodeName;
}

/** The logged-in username. Empty when signed out. */
export function getCurrentUsername(): string {
  return currentUsername;
}

function remember(s: { user?: string; node?: string }) {
  if (s.node) nodeName = s.node;
  if (s.user) currentUsername = s.user;
}

export async function restoreSession(): Promise<SessionInfo | null> {
  try {
    const s = await api.get<SessionInfo>("/api/session", { skipAuthRedirect: true });
    setCsrf(s.csrf);
    remember(s);
    return s;
  } catch {
    return null;
  }
}

/** The wire shape of a freshly minted session (`POST /api/login`, `POST /api/setup`). */
interface SessionResponse {
  ok: boolean;
  csrf: string;
  user: string;
  role?: string;
  vmid?: number | null;
  node?: string;
}

/** Install a just-issued session into module state and hand it back typed. */
function adoptSession(res: SessionResponse): SessionInfo {
  setCsrf(res.csrf);
  remember(res);
  return {
    user: res.user,
    role: res.role as SessionInfo["role"],
    vmid: res.vmid,
    csrf: res.csrf,
    node: res.node,
  };
}

export async function login(username: string, password: string): Promise<SessionInfo> {
  const res = await api.post<SessionResponse>(
    "/api/login",
    { username, password },
    { skipAuthRedirect: true },
  );
  return adoptSession(res);
}

export async function logout(): Promise<void> {
  try {
    await api.post<{ ok: boolean }>("/api/logout");
  } finally {
    setCsrf(null);
    currentUsername = "";
  }
}

// --- First-run setup ------------------------------------------------------
// The panel ships unconfigured: until a user exists, these three unauthenticated
// endpoints are the only way in. They all refuse once setup has completed (409),
// so nothing here is a standing backdoor.

/** The Proxmox connection the wizard collects. `token_secret` never leaves this object. */
export interface SetupPveConnection {
  host: string;
  port: number;
  node: string;
  scheme: "https" | "http";
  token_id: string;
  token_secret: string;
  /** Optional SHA-256 cert pin; "" means "don't pin". */
  fingerprint: string;
  verify_tls: boolean;
}

export interface SetupAccount {
  username: string;
  password: string;
}

export interface SetupFirstUser extends SetupAccount {
  vmid: number;
}

export interface SetupRequest {
  pve: SetupPveConnection;
  admin: SetupAccount;
  /** Optional — omit or send null to create no regular user. */
  user?: SetupFirstUser | null;
}

export interface SetupStatus {
  needed: boolean;
}

export interface SetupTestResult {
  ok: boolean;
  node: string;
  guests: number;
  nodes: string[];
}

/** Cheap, always-available: does this deployment still need first-run setup? */
export function getSetupStatus(): Promise<SetupStatus> {
  return api.get<SetupStatus>("/api/setup/status", { skipAuthRedirect: true });
}

/** Dry-run the Proxmox connection. Persists nothing; throws ApiError on failure. */
export function testSetupConnection(body: SetupPveConnection): Promise<SetupTestResult> {
  return api.post<SetupTestResult>("/api/setup/test", body, { skipAuthRedirect: true });
}

/**
 * Commit the configuration. On success the admin is ALREADY signed in (the
 * backend sets the session cookie), so we adopt the returned session exactly as
 * `login()` does — the caller lands in the panel authenticated.
 */
export async function submitSetup(body: SetupRequest): Promise<SessionInfo> {
  const res = await api.post<SessionResponse>("/api/setup", body, { skipAuthRedirect: true });
  return adoptSession(res);
}

// --- Debug (admin-only, only present when HLIDSKJALF_DEBUG=true) -----------

export interface DebugLogEntry {
  ts: number;
  level: string;
  logger: string;
  message: string;
}

export interface DebugErrorEntry {
  ts: number;
  method: string;
  path: string;
  client?: string | null;
  error: string;
  traceback?: string;
}

export interface DebugConfig {
  [key: string]: unknown;
}

export interface DebugAccumulator {
  running: boolean;
  prev_count: number;
  task_name?: string | null;
}

export interface DebugHealth {
  ok: boolean;
  debug: boolean;
  log_level: string;
  pve_node?: string | null;
  db_path?: string;
  metrics_source?: string | null;
  state_keys?: string[];
  accumulator?: DebugAccumulator;
}

export const debug = {
  getHealth: () => api.get<DebugHealth>("/api/debug/health"),
  getConfig: () => api.get<DebugConfig>("/api/debug/config"),
  getLogs: () => api.get<DebugLogEntry[]>("/api/debug/logs"),
  getErrors: () => api.get<DebugErrorEntry[]>("/api/debug/errors"),
  getAccumulator: () => api.get<DebugAccumulator>("/api/debug/accumulator"),
};
