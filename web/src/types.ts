// Shapes returned by the backend (see http://localhost:8000/docs)
export type User = { id: string; email: string; name: string; email_verified: boolean };
export type TokenOut = { access_token: string; user: User };

export type TripSummary = { id: string; name: string; city: string; start_date: string; end_date: string; member_count: number };
export type Member = { user_id: string; name: string; role: string; is_me: boolean; preferences_done: boolean; free_dates: string[] };
export type Trip = TripSummary & {
  invite_code: string; invite_url: string; members: Member[];
  setup: { members: number; preferences_done: number; waiting_for: string[] };
};

// The 10 taste features, in the fixed order from TS SRS/SDD 1.1 (section 6.1).
// The same order is used by the survey, the database, the contracts and the recommender.
export const CATEGORIES = ["food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival"] as const;
export type Category = (typeof CATEGORIES)[number]; // = "food" | "cafe" | ... | "festival"

export type Preferences = {
  ratings: Record<Category, number>; // every feature → whole number 1..5 (raw score; the recommender makes weights)
  version: number;                   // 0 = never saved; every save adds 1
  submitted?: boolean;
};
export type Exclusions = { tags: string[]; poi_ids: string[] }; // personal "never take me there" rules

export type FieldStatus = Record<string, string>; // e.g. { hours: "UNKNOWN", cost: "UNKNOWN", duration: "ESTIMATED" }
export type Place = {
  id: string; name: string; area: string; kind: string; category: string; features: Category[];
  indoor: boolean | null; avg_cost_krw: number | null; duration_min: number;
  image_url: string | null; place_url: string | null; lat: number; lng: number; field_status: FieldStatus;
  enriched_metadata?: EnrichedMeta;  // extra info from the data pipeline; missing values are null
};
/** Enriched place info (travelsync_poi_metadata_enriched_v3.csv). Every value can be null = unknown. */
export type EnrichedMeta = Partial<Record<string, string | null>>;
export type Reason = { code: string; text: string; user_id?: string };
export type GroupPick = {
  poi_id: string; group_rank: number; mean_score: number; selection_gain: number | null; group_utility: number;
  member_scores: Record<string, number>; picked_for: string | null; reasons: Reason[]; place: Place | null;
  fit: number | null; member_fit: Record<string, number | null>;  // null for uncalibrated hybrid relevance
};
export type Removed = { poi_id: string; stage: string; rule: "member_exclusion" | "misery" | "no_features"; detail: string; place: Place | null };
export type Method = "gfar" | "average" | "awm";
export type Recommendations = {
  run_id: number; created_at: string; model_version: string; method: Method; awm_threshold: number | null;
  dataset_version: string; feature_schema_version: string; score_scale: "1-5" | "0-1" | "relative_relevance_0_1"; members: Record<string, string>;
  group: GroupPick[]; removed: Removed[];
  metrics: { eligible?: number; candidate_pool?: number; returned?: number; per_member_relevance?: Record<string, number>; min_member_relevance?: number };
};

export type PlanItem = {
  poi_id: string; start: string; end: string; locked: boolean; moved_from: string | null;
  availability: { available: boolean | null; close?: string | null } | null; // null when the checker is off; available null = hours unknown
  place: Place | null; mean_score: number | null;
};
export type Plan = {
  id: number; letter: string; variant: string; name: string; votes: number;
  stats: { group_fit: number | null; cost_per_person: number; cost_unknown_count?: number; walking_km_per_day: number | null };
  days: { date: string; items: PlanItem[] }[];
};
export type PlansView = {
  plans: Plan[]; my_vote: number | null; voted: number; members: number; chosen_id: number | null;
  holidays: { date: string; name: string }[];
  availability_checked: boolean;
};
export type Features = { availability: boolean };
export type PoiDetail = Place & {
  name_ko: string | null; address: string; phone: string | null; tags: string[]; feature_rules: string; source: string;
  dataset_version: string; closed_on_public_holidays?: boolean;
  hours: { day: string; open: string | null; close: string | null }[]; // Mon…Sun; open null = closed that day; [] = unknown
};
export type Availability = { poi_id: string; available: boolean | null; reason: string | null; open: string | null; close: string | null; next_available: string | null };

// Onboarding survey ("travel tastes"), see GET /meta/tastes and GET/PUT /me/tastes
export type TasteOption = { code: string; label_ko: string; label_en: string; keywords: string[]; exclusion: string | null };
export type TasteQuestion = { code: string; label_ko: string; label_en: string; options: TasteOption[] };
export type TastesMeta = { version: string; max_picks: number; questions: TasteQuestion[] };
export type Tastes = {
  status: "done" | "skipped" | null; version: string; answers: Record<string, string[]>;
  keywords: string[]; avoid_keywords: string[]; exclusion_tags: string[];
};
