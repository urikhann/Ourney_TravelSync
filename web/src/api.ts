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
  routing_credentials_rejected: "Kakao rejected the routing key or API permission. Check the planning-data service's REST API key and Kakao Mobility access, then restart that service.",
  kakao_rest_api_key_not_configured: "The planning-data service has no Kakao REST API key. Configure it and restart that service.",
  routing_quota_exceeded: "Kakao's routing request limit was reached. Check the API quota and try again later.",
  routing_request_rejected: "Kakao rejected a routing request. Check the planning-data logs for its HTTP status and provider error code.",
  routing_provider_error: "Kakao rejected the routing request. Check the planning-data service and Kakao API configuration.",
  routing_provider_unavailable: "The planning-data service could not reach Kakao routing. Check its network connection and try again.",
  routing_invalid_response: "Kakao returned no usable route data. Check the planning-data service before retrying.",
  routing_start_point_unreachable: "No driving routes from a day's start location reach its shortlisted places. Choose a road-accessible entrance, parking area, or another start location.",
  routing_end_point_unreachable: "No driving routes from a day's shortlisted places reach its end location. Choose a road-accessible entrance, parking area, or another end location.",
  routing_cache_unavailable: "The saved route cache is unavailable. Check the planning-data cache volume before retrying.",
  routing_no_routes: "Kakao could not find driving routes for these locations. Check the start/end points and selected places.",
  planning_candidates_unavailable: "The catalogue candidate service is unavailable. Try again after it restarts.",
  planning_candidates_invalid_response: "The catalogue candidates did not match this trip's saved preferences.",
  optimizer_unavailable: "The itinerary optimizer is unavailable. Your saved plan is kept.",
  optimizer_insufficient_candidates: "Too few places meet the route and scheduling requirements for every day. Review the excluded places below.",
  optimizer_no_feasible_itinerary: "No itinerary fits the dates, travel limits, and daily meal/activity rules. Check that enough restaurants and different activities are available, or extend the day.",
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
