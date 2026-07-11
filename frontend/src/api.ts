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

async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
  opts?: { skipAuthRedirect?: boolean },
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-Hlidskjalf-CSRF"] = csrfToken;

  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

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

  return (await res.json()) as T;
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
  csrf: string;
}

export async function restoreSession(): Promise<SessionInfo | null> {
  try {
    const s = await api.get<SessionInfo>("/api/session", { skipAuthRedirect: true });
    setCsrf(s.csrf);
    return s;
  } catch {
    return null;
  }
}

export async function login(username: string, password: string): Promise<void> {
  const res = await api.post<{ ok: boolean; csrf: string }>(
    "/api/login",
    { username, password },
    { skipAuthRedirect: true },
  );
  setCsrf(res.csrf);
}

export async function logout(): Promise<void> {
  try {
    await api.post<{ ok: boolean }>("/api/logout");
  } finally {
    setCsrf(null);
  }
}
