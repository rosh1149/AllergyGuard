const TOKEN_KEY = "allergyguard.token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable (private mode): the session lasts until reload.
  }
}

function messageFrom(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: [{loc, msg}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc ?? []).filter((p) => p !== "body").join(" ");
        const msg = (d.msg ?? "Invalid value").replace(/^Value error, /, "");
        return field ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (status >= 500) return "The server had a problem. Please try again.";
  return `Request failed (${status}).`;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, headers, body });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection.");
  }
  if (res.status === 401 && token) {
    setToken(null);
    window.dispatchEvent(new Event("allergyguard:logout"));
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, res.status));
  return data as T;
}
