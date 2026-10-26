// The preference survey: 10 taste features (TS SRS/SDD 1.1) + personal "never take me there" rules.
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, message } from "../api";
import { CATEGORIES, type Category, type Exclusions, type Preferences as Prefs } from "../types";
import { ErrorBox, Loading } from "../ui";
import RatingRow from "../components/RatingRow";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import { CableCar, Clapperboard, Coffee, Landmark, Palette, PartyPopper, Plus, ShoppingBag, Trees, Utensils, Waves, X,
  type LucideIcon } from "lucide-react";

// ---- 1. Static content: what to show for each feature --------------------------------
// Record<Category, ...> forces us to describe ALL 10 features. Forget one → TypeScript error.
const CATEGORY_INFO: Record<Category, { Icon: LucideIcon; label: string; ko: string; hint: string }> = {
  food:          { Icon: Utensils,     label: "Food & local eats",        ko: "맛집과 음식",         hint: "Restaurants, local dishes, street food" },
  cafe:          { Icon: Coffee,       label: "Cafés & desserts",         ko: "카페와 디저트",       hint: "Coffee shops, bakeries, dessert cafés" },
  nature:        { Icon: Waves,        label: "Nature & views",           ko: "자연과 경관",         hint: "Beaches, parks, mountains, viewpoints" },
  history:       { Icon: Landmark,     label: "History & tradition",      ko: "역사와 전통",         hint: "Temples, heritage sites, old villages" },
  art:           { Icon: Palette,      label: "Art, exhibitions & shows", ko: "예술과 전시 및 공연", hint: "Museums, galleries, theatres" },
  shopping:      { Icon: ShoppingBag,  label: "Shopping",                 ko: "쇼핑",               hint: "Markets, malls, shopping streets" },
  activity:      { Icon: CableCar,     label: "Activities & experiences", ko: "체험과 액티비티",     hint: "Hands-on classes, sports, walking courses" },
  entertainment: { Icon: Clapperboard, label: "Fun & entertainment",      ko: "놀이와 엔터테인먼트", hint: "Theme parks, VR, games, cinemas" },
  relax:         { Icon: Trees,        label: "Rest & walks",             ko: "휴식과 산책",         hint: "Quiet parks, promenades, spas" },
  festival:      { Icon: PartyPopper,  label: "Festivals & events",       ko: "축제와 이벤트",       hint: "Events on your travel dates" },
};

// Personal hard rules. Different from a low rating: a "1" only lowers a score, these remove places completely.
const EXCLUSIONS: { value: string; label: string }[] = [
  { value: "hiking", label: "No hiking" },
  { value: "raw_fish", label: "No raw fish" },
  { value: "alcohol", label: "No bars or pubs" },
];

export default function Preferences() {
  // ---- 2. Inputs: which trip? (from the URL /trips/:id/preferences) ------------------
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  // ---- 3. Load my saved answers from the backend -------------------------------------
  const saved = useQuery({ queryKey: ["prefs", id], queryFn: () => api.get<Prefs>(`/trips/${id}/preferences/me`) });
  const savedEx = useQuery({ queryKey: ["exclusions", id], queryFn: () => api.get<Exclusions>(`/trips/${id}/exclusions/me`) });

  // ---- 4. Local state: the answers while the user is editing --------------------------
  const [form, setForm] = useState<Prefs | null>(null);
  const [ex, setEx] = useState<Exclusions | null>(null);
  useEffect(() => { if (saved.data && !form) setForm(saved.data); }, [saved.data, form]);
  useEffect(() => { if (savedEx.data && !ex) setEx(savedEx.data); }, [savedEx.data, ex]);

  // ---- 5. Saving: two separate inputs (SDD: taste scores vs. personal exclusions) -----
  const save = useMutation({
    mutationFn: async ({ p, e }: { p: Prefs; e: Exclusions }) => {
      // expected_version: if I saved on another device in between, the server answers 409 instead of overwriting
      const result = await api.put<Prefs>(`/trips/${id}/preferences/me`, { ratings: p.ratings, expected_version: p.version });
      await api.put<Exclusions>(`/trips/${id}/exclusions/me`, e);
      return result;
    },
    onSuccess: (result) => {
      qc.setQueryData(["prefs", id], result);
      qc.invalidateQueries({ queryKey: ["exclusions", id] });
      qc.invalidateQueries({ queryKey: ["trip", id] });  // the trip room must refetch ("Preferences ✓")
      nav(`/trips/${id}`);
    },
  });

  if (saved.isError || savedEx.isError) return <><TopBar title="My preferences" back={`/trips/${id}`} /><main className="content"><ErrorBox error={message(saved.error ?? savedEx.error)} /></main></>;
  if (!form || !ex) return <><TopBar title="My preferences" back={`/trips/${id}`} /><Loading /></>;

  // ---- 6. Small helpers that create a NEW object (never change state in place) -------
  const setRating = (c: Category, v: number) => setForm({ ...form, ratings: { ...form.ratings, [c]: v } });
  const toggle = (tag: string) =>
    setEx({ ...ex, tags: ex.tags.includes(tag) ? ex.tags.filter((t) => t !== tag) : [...ex.tags, tag] });

  const loved = CATEGORIES.filter((c) => form.ratings[c] >= 4).map((c) => CATEGORY_INFO[c].label);

  // ---- 7. What to draw ----------------------------------------------------------------
  return (
    <>
      <TopBar title="My preferences" back={`/trips/${id}`} />
      <main className="content">
        <h2 className="title">What do you enjoy on a trip?</h2>
        <p className="muted small">1 = not for me · 5 = must do. All 10 are needed.</p>

        <div className="card list">
          {CATEGORIES.map((c) => (
            <RatingRow
              key={c}
              icon={(() => { const I = CATEGORY_INFO[c].Icon; return <I size={16} />; })()}
              label={CATEGORY_INFO[c].label}
              hint={`${CATEGORY_INFO[c].ko} · ${CATEGORY_INFO[c].hint}`}
              value={form.ratings[c]}
              onChange={(v) => setRating(c, v)}
            />
          ))}
        </div>

        <div className="h2">Never take me to <small>removes places completely</small></div>
        <div className="chips">
          {EXCLUSIONS.map((k) => {
            const on = ex.tags.includes(k.value);
            return (
              <button key={k.value} type="button" aria-pressed={on} onClick={() => toggle(k.value)}
                className={"chip " + (on ? "rose" : "dashed")} style={{ padding: "8px 12px" }}>
                {on ? <X size={13} /> : <Plus size={13} />} {k.label}
              </button>
            );
          })}
        </div>
        <p className="muted tiny" style={{ marginTop: 6 }}>Budget, dates and how far to travel are set for the whole group.</p>

        {loved.length > 0 && (
          <div className="banner blue" style={{ marginTop: 16 }}>You love: {loved.join(", ")}</div>
        )}

        <div style={{ height: 12 }} />
        <ErrorBox error={save.error ? message(save.error) : null} />
        <button className="btn" style={{ marginTop: 8 }} disabled={save.isPending} onClick={() => save.mutate({ p: form, e: ex })}>
          {save.isPending ? "Saving…" : "Save preferences"}
        </button>
        {form.version > 0 && <p className="muted tiny center" style={{ marginTop: 8 }}>Saved version {form.version}</p>}
      </main>
      <TabBar tripId={id} />
    </>
  );
}
