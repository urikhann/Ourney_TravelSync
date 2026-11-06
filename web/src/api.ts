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
  planning_data_not_configured: "Verified operating and route data are not configured yet. Your recommendations and any saved plan are kept.",
  planning_data_unavailable: "The operating and routing service is unavailable. Your saved plan is kept.",
  planning_data_invalid_response: "The operating or routing data did not match this trip. Your saved plan is kept.",
  optimizer_unavailable: "The itinerary optimizer is unavailable. Your saved plan is kept.",
  optimizer_insufficient_candidates: "Too few places have verified hours, costs and routes for every day. Review the excluded places below.",
  optimizer_no_feasible_itinerary: "No itinerary satisfies these dates, budget and travel limits. Adjust the conditions explicitly and try again.",
  optimizer_search_timeout: "The search ended without a verified itinerary. Your saved plan is kept; you can try again.",
  optimizer_inputs_changed: "The group or recommendations changed during planning. Reload and try again.",
  optimizer_member_unavailable: "Every member must confirm availability for every trip day before planning.",
  optimizer_requires_gfar: "Generate the group's GFAR recommendations before building an itinerary.",
  recommendations_stale: "Preferences or group members changed. Generate recommendations again before planning.",
  optimizer_recommendation_data_missing: "Regenerate recommendations to include all member scores and relevance values needed for planning.",
  optimizer_owner_required: "Only the trip owner can set planning conditions and generate the itinerary.",
  optimizer_final_validation_failed: "The returned itinerary failed the final checks. It was not published; your saved plan is kept.",
  optimizer_invalid_solver_model: "The optimizer could not validate its model. Your saved plan is kept.",
  optimizer_invalid_response: "The optimizer returned an invalid response. Your saved plan is kept.",
  optimizer_snapshot_mismatch: "The optimizer response did not match this request. Your saved plan is kept.",
  recommender_unavailable: "The recommender isn't running. Start the mock service (port 8001).",
};

export function message(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code.startsWith("waiting_for_preferences")) return "Still waiting for: " + e.code.split(": ")[1];
    return MESSAGES[e.code] ?? `Something went wrong (${e.code}).`;
  }
  return "Something went wrong.";
}
