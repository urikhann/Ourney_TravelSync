// One place for every call to the backend. Screens never call fetch() directly.
const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const KEY = "ts_token";

export const getToken = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
export const setToken = (t: string | null) => {
  try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch { /* private mode */ }
};

export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error");
  }
  if (res.status === 401 && token) {
    setToken(null);
    const next = encodeURIComponent(location.pathname);
    location.assign(`/login?next=${next}`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as { detail?: unknown }).detail;
    throw new ApiError(res.status, typeof d === "string" ? d : Array.isArray(d) ? "invalid_input" : "request_failed");
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
};

const MESSAGES: Record<string, string> = {
  network_error: "Can't reach the server. Is the backend running?",
  wrong_email_or_password: "Email or password is wrong.",
  email_not_verified: "Please verify your email first — we just sent you a new code.",
  email_already_registered: "That email already has an account. Log in instead.",
  wrong_code: "That code isn't right. Try again.",
  code_expired: "That code has expired. Send a new one.",
  too_many_attempts: "Too many tries. Send a new code.",
  invalid_input: "Please check the fields and try again.",
  invite_not_found: "This invite link doesn't work any more.",
  trip_not_found: "Trip not found.",
  no_recommendations_yet: "No recommendations yet.",
  recommender_unavailable: "The recommender isn't running. Start the mock service (port 8001).",
};

export function message(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code.startsWith("waiting_for_preferences")) return "Still waiting for: " + e.code.split(": ")[1];
    return MESSAGES[e.code] ?? `Something went wrong (${e.code}).`;
  }
  return "Something went wrong.";
}
