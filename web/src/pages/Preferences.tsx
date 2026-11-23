// This trip's preferences (the trip's "Me" tab), issue #21:
//   - "Use my saved preferences": the same 10 ratings as my profile (copied when I joined or created the trip)
//   - "Customize for this trip": change the ratings here only; my saved preferences stay the same,
//     unless I tick "Also save as my preferences for future trips".
// First time ever (nothing saved yet): just the survey; the answers also become my saved preferences.
// Personal "never take me there" rules are always per trip.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Sparkles, X } from "lucide-react";
import { api, message } from "../api";
import type { Category, Exclusions, Preferences as Prefs } from "../types";
import { ErrorBox, Loading } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import RatingsList, { loved } from "../components/RatingsList";
import { t as tr } from "../i18n";

// Personal hard rules. Different from a low rating: a "1" only lowers a score, these remove places completely.
const EXCLUSIONS: { value: string; label: string }[] = [
  { value: "hiking", label: "No hiking" },
  { value: "raw_fish", label: "No raw fish" },
  { value: "alcohol", label: "No bars or pubs" },
];

export default function Preferences() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  const saved = useQuery({ queryKey: ["prefs", id], queryFn: () => api.get<Prefs>(`/trips/${id}/preferences/me`) });
  const savedEx = useQuery({ queryKey: ["exclusions", id], queryFn: () => api.get<Exclusions>(`/trips/${id}/exclusions/me`) });

  const [form, setForm] = useState<Prefs | null>(null);
  const [ex, setEx] = useState<Exclusions | null>(null);
  const [mode, setMode] = useState<"saved" | "custom">("saved");
  const [asDefault, setAsDefault] = useState(false);
  useEffect(() => {
    if (saved.data && !form) { setForm(saved.data); setMode(saved.data.source === "custom" ? "custom" : "saved"); }
  }, [saved.data, form]);
  useEffect(() => { if (savedEx.data && !ex) setEx(savedEx.data); }, [savedEx.data, ex]);

  const hasSaved = (form?.saved_version ?? 0) > 0;
  const save = useMutation({
    mutationFn: async ({ p, e }: { p: Prefs; e: Exclusions }) => {
      // expected_version: if I saved on another device in between, the server answers 409 instead of overwriting
      const result = hasSaved && mode === "saved"
        ? (p.source === "saved" ? p : await api.post<Prefs>(`/trips/${id}/preferences/me/use-saved`))
        : await api.put<Prefs>(`/trips/${id}/preferences/me`, { ratings: p.ratings, expected_version: p.version, save_as_default: asDefault });
      await api.put<Exclusions>(`/trips/${id}/exclusions/me`, e);
      return result;
    },
    onSuccess: (result) => {
      qc.setQueryData(["prefs", id], result);
      qc.invalidateQueries({ queryKey: ["exclusions", id] });
      qc.invalidateQueries({ queryKey: ["trip", id] });  // the trip room must refetch ("Preferences ✓")
      qc.invalidateQueries({ queryKey: ["my-prefs"] });
      nav(`/trips/${id}`);
    },
  });

  if (saved.isError || savedEx.isError) return <><TopBar title={tr("My preferences")} back={`/trips/${id}`} /><main className="content"><ErrorBox error={message(saved.error ?? savedEx.error)} /></main></>;
  if (!form || !ex) return <><TopBar title={tr("My preferences")} back={`/trips/${id}`} /><Loading /></>;

  const setRating = (c: Category, v: number) => { setForm({ ...form, ratings: { ...form.ratings, [c]: v } }); setMode("custom"); };
  const toggle = (tag: string) => setEx({ ...ex, tags: ex.tags.includes(tag) ? ex.tags.filter((t) => t !== tag) : [...ex.tags, tag] });
  const likes = loved(form.ratings);

  return (
    <>
      <TopBar title={tr("My preferences")} back={`/trips/${id}`} />
      <main className="content">
        <h2 className="title">{tr("What do you enjoy on a trip?")}</h2>

        {hasSaved ? (
          <>
            <div className="seg" style={{ gridTemplateColumns: "1fr 1fr", margin: "12px 0" }}>
              <button type="button" className={mode === "saved" ? "on" : ""} onClick={() => setMode("saved")}>{tr("Use my saved preferences")}</button>
              <button type="button" className={mode === "custom" ? "on" : ""} onClick={() => setMode("custom")}>{tr("Customize for this trip")}</button>
            </div>
            {mode === "saved" ? (
              <div className="card pad saved-box">
                <div className="row" style={{ gap: 10 }}>
                  <span className="ico-sq green"><Sparkles size={17} /></span>
                  <div className="grow">
                    <div className="b">{tr("Same as your profile")}</div>
                    <div className="muted tiny">{tr("No survey needed. Changes to this trip never change your profile.")}</div>
                  </div>
                </div>
                {likes.length > 0 && <div className="chips" style={{ marginTop: 12 }}>{likes.map((l) => <span key={l} className="chip blue">{l}</span>)}</div>}
                <Link className="saved-edit small" to="/me"><Pencil size={13} /> {tr("Edit my saved preferences")}</Link>
              </div>
            ) : (
              <>
                <p className="muted small">{tr("1 = not for me · 5 = must do. Only this trip changes.")}</p>
                <RatingsList ratings={form.ratings} onChange={setRating} />
                <label className="pf-switch" style={{ margin: "12px 4px" }}>
                  <input type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />
                  <span className="track" aria-hidden="true" /> {tr("Also save as my preferences for future trips")}
                </label>
              </>
            )}
          </>
        ) : (
          <>
            <p className="muted small">{tr("1 = not for me · 5 = must do. All 10 are needed.")}</p>
            <div className="banner blue" style={{ marginBottom: 10 }}>{tr("Answer once: these become your saved preferences and fill in your next trips automatically.")}</div>
            <RatingsList ratings={form.ratings} onChange={setRating} />
          </>
        )}

        <div className="h2">{tr("Never take me to")} <small>{tr("removes places completely")}</small></div>
        <div className="chips">
          {EXCLUSIONS.map((k) => {
            const on = ex.tags.includes(k.value);
            return (
              <button key={k.value} type="button" aria-pressed={on} onClick={() => toggle(k.value)}
                className={"chip " + (on ? "rose" : "dashed")} style={{ padding: "8px 12px" }}>
                {on ? <X size={13} /> : <Plus size={13} />} {tr(k.label)}
              </button>
            );
          })}
        </div>
        <p className="muted tiny" style={{ marginTop: 6 }}>{tr("Dates are set for the whole group in the trip room.")}</p>

        <div style={{ height: 12 }} />
        <ErrorBox error={save.error ? message(save.error) : null} />
        <button className="btn" style={{ marginTop: 8 }} disabled={save.isPending} onClick={() => save.mutate({ p: form, e: ex })}>
          {save.isPending ? tr("Saving…") : hasSaved && mode === "saved" ? tr("Use for this trip") : tr("Save preferences")}
        </button>
        {form.version > 0 && <p className="muted tiny center" style={{ marginTop: 8 }}>{tr("Saved version {n}", { n: form.version })}</p>}
      </main>
      <TabBar tripId={id} />
    </>
  );
}
