// Planning conditions, in 2 quick steps:
//   1. When: tap a start and a return time (or pick another time)
//   2. Where: search a start place (station, hotel, address, or "my location"); the end is the same place unless changed
// The same choices apply to every day unless "Same every day" is switched off.
// Must-visit places, visit durations and travel limits are optional, under "More options".
// The request sent to /itineraries/generate is the same as before (OptimizationInput).
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Car, ChevronRight, Clock, Flag, Loader, MapPin, Plus, Sparkles, X } from "lucide-react";
import { api, message } from "../api";
import { t } from "../i18n";
import type { OptimizationInput, PlanningPoint, PlansView } from "../types";
import { ErrorBox, fmtDate, weekday } from "../ui";
import LocationPicker from "./LocationPicker";

const clock = (minute: number) => `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
const minute = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
const LEAVE = ["08:00", "09:00", "10:00", "11:00"];
const BACK = ["18:00", "20:00", "21:00", "22:00"];
const samePoint = (a?: PlanningPoint | null, b?: PlanningPoint | null) => !!a && !!b && a.latitude === b.latitude && a.longitude === b.longitude;

type Day = { date: string; start: string; end: string; from: PlanningPoint | null; to: PlanningPoint | null };

function TimeChips({ label, value, presets, onChange, midnight }: { label: string; value: string; presets: string[]; onChange: (v: string) => void; midnight?: boolean }) {
  const [custom, setCustom] = useState(!!value && !presets.includes(value) && !(midnight && value === "00:00"));
  return (
    <div className="pf-time">
      <div className="pf-label">{label}</div>
      <div className="chips">
        {presets.map((p) => <button key={p} type="button" className={"chip " + (value === p && !custom ? "dark" : "white")} onClick={() => { setCustom(false); onChange(p); }}>{p}</button>)}
        {midnight && <button type="button" className={"chip " + (value === "00:00" && !custom ? "dark" : "white")} onClick={() => { setCustom(false); onChange("00:00"); }}>{t("Midnight")}</button>}
        {custom ? <input type="time" className="chip time-input" aria-label={label} value={value} autoFocus onChange={(e) => onChange(e.target.value)} />
          : <button type="button" className="chip dashed" onClick={() => setCustom(true)}><Clock size={12} /> {t("Other")}</button>}
      </div>
    </div>
  );
}

function PlaceButton({ icon, label, value, onClick }: { icon: React.ReactNode; label: string; value: PlanningPoint | null; onClick: () => void }) {
  return (
    <button type="button" className={"pf-place" + (value ? " set" : "")} onClick={onClick}>
      <span className="pf-place-ico">{icon}</span>
      <span className="grow"><span className="pf-label">{label}</span><span className="b">{value?.label ?? t("Search a station, hotel or address")}</span></span>
      <ChevronRight size={17} color="var(--soft)" />
    </button>
  );
}

export default function OptimizationForm({ tripId, view }: { tripId: string; view: PlansView }) {
  const qc = useQueryClient();
  const prior = view.latest_optimization?.configuration;
  const points = view.setup.points;
  const composition = prior?.daily_composition ?? view.setup.default_composition;
  const dates = view.setup.dates;

  const [daily, setDaily] = useState<Day[]>(() => dates.map((date) => {
    const old = prior?.days.find((d) => d.date === date);
    return { date, start: old ? clock(old.start_min) : "10:00", end: old ? clock(old.end_min) : "20:00",
      from: old?.start_point ?? null, to: old?.end_point ?? null };
  }));
  const [sameAll, setSameAll] = useState(() => !prior || daily.every((d) => d.start === daily[0].start && d.end === daily[0].end
    && samePoint(d.from, daily[0].from) && samePoint(d.to, daily[0].to)));
  const [sameEnd, setSameEnd] = useState(() => !prior || daily.every((d) => samePoint(d.from, d.to)));
  const [editing, setEditing] = useState(0);              // which day is shown when days differ
  const [picker, setPicker] = useState<"from" | "to" | null>(null);

  // ---- optional: must-visit places, visit durations, travel limits ----
  const [breakfast, setBreakfast] = useState(composition.breakfast_enabled || composition.meal_windows.some(w => w.name === "breakfast"));
  const [locks, setLocks] = useState(prior?.locked_items ?? []);
  const [durations, setDurations] = useState<Record<string, number>>(prior?.duration_overrides ?? {});
  const [names, setNames] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<{ id: string; name: string; duration_min?: number } | null>(null);
  const [chosenDay, setChosenDay] = useState(dates[0] ?? "");
  const [duration, setDuration] = useState("");
  const [leg, setLeg] = useState(prior?.max_leg_minutes == null ? "" : String(prior.max_leg_minutes));
  const [walk, setWalk] = useState(prior?.max_total_walk_minutes == null ? "" : String(prior.max_total_walk_minutes));
  const [eveningSearch, setEveningSearch] = useState("");
  const [eveningDay, setEveningDay] = useState(dates[0] ?? "");
  const evening = useQuery({ queryKey: ["evening-choices", tripId, eveningSearch],
    queryFn: () => api.get<{ id: string; name: string; category: string; hours_verified: boolean }[]>(
      `/trips/${tripId}/evening-choices?q=${encodeURIComponent(eveningSearch)}`),
    enabled: view.setup.is_owner && view.setup.recommendations_ready });
  const searchQuery = useQuery({ queryKey: ["planning-places", tripId, search],
    queryFn: () => api.get<{ id: string; name: string; duration_min?: number }[]>(`/trips/${tripId}/planning-places?q=${encodeURIComponent(search)}`),
    enabled: view.setup.is_owner && view.setup.recommendations_ready && search.trim().length >= 2 });
  const results = search.trim().length >= 2 ? (searchQuery.data ?? []).slice(0, 6)
    : points.slice(0, 6).map((p) => ({ id: p.id, name: p.name, duration_min: p.duration_min }));
  const nameOf = (id: string) => names[id] ?? points.find((p) => p.id === id)?.name ?? id;

  const generate = useMutation({
    mutationFn: (body: OptimizationInput) => api.post<PlansView>(`/trips/${tripId}/itineraries/generate`, body),
    onSuccess: (result) => qc.setQueryData(["plans", tripId], result),
    onError: () => { void qc.invalidateQueries({ queryKey: ["plans", tripId] }); },
  });

  const rows = useMemo(() => daily.map((d, i) => (sameAll && i ? { ...daily[0], date: d.date } : d)), [daily, sameAll]);
  const problems = rows.flatMap((r) => [
    !r.from && t("{day}: choose where you start", { day: weekday(r.date) }),
    !sameEnd && !r.to && t("{day}: choose where you finish", { day: weekday(r.date) }),
    r.end !== "00:00" && minute(r.end) <= minute(r.start) && t("{day}: return time must be after the start", { day: weekday(r.date) }),
  ].filter(Boolean) as string[]);

  if (!view.setup.is_owner) return <p className="muted small">{t("The trip owner sets the conditions and builds the itinerary.")}</p>;
  if (!view.setup.recommendations_ready) return <p className="muted small">{t("First")} <Link to={`/trips/${tripId}/places`}>{t("generate GFAR recommendations")}</Link>.</p>;

  const cur = sameAll ? 0 : editing;
  const row = daily[cur];
  const set = (patch: Partial<Day>) => setDaily(daily.map((d, j) => (j === cur ? { ...d, ...patch } : d)));

  const submit = () => {
    if (problems.length) return;
    generate.mutate({
      budget_total: 0, transport_mode: "drive",
      gap_policy: prior?.gap_policy,
      max_leg_minutes: leg === "" ? null : Number(leg), max_total_walk_minutes: walk === "" ? null : Number(walk),
      daily_composition: { ...composition, breakfast_enabled: breakfast,
        meal_windows: breakfast ? composition.meal_windows : composition.meal_windows.filter(w => w.name !== "breakfast"),
        meal_overrides: breakfast ? composition.meal_overrides : composition.meal_overrides?.filter(w => w.name !== "breakfast") }, duration_overrides: durations,
      required_access: prior?.required_access ?? [], locked_items: locks,
      days: rows.map((r) => ({ date: r.date, start_min: minute(r.start), end_min: r.end === "00:00" ? 1440 : minute(r.end),
        start_point: r.from!, end_point: sameEnd ? r.from! : r.to!,
        breaks: prior?.days.find(d => d.date === r.date)?.breaks })),
    });
  };

  return (
    <form className="plan-form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="pf-head">
        <div className="pf-title">{t("Plan your days")}</div>
        <div className="muted small">{t("Two quick choices. TravelSync picks the places and the order.")}</div>
      </div>

      {dates.length > 1 && (
        <div className="pf-days">
          <label className="pf-switch">
            <input type="checkbox" checked={sameAll} onChange={(e) => {
              setSameAll(e.target.checked); setEditing(0);
              if (!e.target.checked) setDaily(daily.map((d, i) => (i ? { ...daily[0], date: d.date } : d)));  // start each day from day 1
            }} />
            <span className="track" aria-hidden="true" /> {t("Same times and places every day")}
          </label>
          {!sameAll && (
            <div className="seg" style={{ gridTemplateColumns: `repeat(${dates.length}, 1fr)`, marginTop: 10 }}>
              {dates.map((d, i) => <button key={d} type="button" className={i === editing ? "on" : ""} onClick={() => setEditing(i)}>{t("Day {n}", { n: i + 1 })} · {weekday(d)}</button>)}
            </div>
          )}
        </div>
      )}

      <section className="pf-step">
        <div className="pf-step-head"><span className="pf-num">1</span>{t("When are you out?")}
          {!sameAll && <span className="muted tiny" style={{ marginLeft: "auto" }}>{fmtDate(row.date)}</span>}</div>
        <TimeChips key={`s${cur}`} label={t("Leave at")} value={row.start} presets={LEAVE} onChange={(v) => set({ start: v })} />
        <TimeChips key={`e${cur}`} label={t("Back by")} value={row.end} presets={BACK} midnight onChange={(v) => set({ end: v })} />
      </section>

      <section className="pf-step">
        <div className="pf-step-head"><span className="pf-num">2</span>{t("Where do you start and finish?")}</div>
        <PlaceButton icon={<MapPin size={17} />} label={t("Start")} value={row.from} onClick={() => setPicker("from")} />
        <label className="pf-switch" style={{ margin: "12px 2px" }}>
          <input type="checkbox" checked={sameEnd} onChange={(e) => setSameEnd(e.target.checked)} />
          <span className="track" aria-hidden="true" /> {t("Finish at the same place")}
        </label>
        {!sameEnd && <PlaceButton icon={<Flag size={17} />} label={t("Finish")} value={row.to} onClick={() => setPicker("to")} />}
        <div className="row muted tiny" style={{ marginTop: 8, gap: 6 }}><Car size={13} style={{ flex: "none" }} /> {t("Travel between places is by car (Kakao driving routes).")}</div>
      </section>

      <details className="pf-more">
        <summary>{t("More options")} <span className="muted tiny">{t("must-visit places, visit time, travel limits")}</span></summary>
        <label className="pf-switch" style={{ marginTop: 12 }}>
          <input type="checkbox" checked={breakfast} onChange={(e) => setBreakfast(e.target.checked)} />
          <span className="track" aria-hidden="true" /> {t("Try to include breakfast")}
        </label>
        {breakfast && <p className="muted small">{t("Breakfast is optional. The planner uses menu or category clues and opening hours. Serving times may need confirmation.")}</p>}
        <div className="pf-label" style={{ marginTop: 12 }}>{t("Must-visit places")}</div>
        <div className="loc-search small-search">
          <input value={search} onChange={(e) => setSearch(e.target.value)} maxLength={120} placeholder={t("Search all places (2+ letters)")} aria-label={t("Search places")} />
        </div>
        <ErrorBox error={searchQuery.error ? message(searchQuery.error) : null} />
        <div className="chips" style={{ marginTop: 8 }}>
          {results.map((p) => (
            <button key={p.id} type="button" className={"chip " + (chosen?.id === p.id ? "dark" : "white")}
              onClick={() => { setChosen(p); setNames((o) => ({ ...o, [p.id]: p.name }));
                setDuration(p.id in durations ? String(durations[p.id]) : p.duration_min == null ? "" : String(p.duration_min)); }}>{p.name}</button>
          ))}
        </div>
        {chosen && (
          <div className="pf-chosen">
            <div className="b">{chosen.name}</div>
            <div className="chips" style={{ margin: "8px 0" }}>
              {dates.map((d, i) => <button key={d} type="button" className={"chip " + (chosenDay === d ? "blue" : "white")} onClick={() => setChosenDay(d)}>{t("Day {n}", { n: i + 1 })}</button>)}
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button type="button" className="btn sm grow" disabled={locks.some((l) => l.poi_id === chosen.id) || locks.length >= 60}
                onClick={() => setLocks([...locks, { poi_id: chosen.id, date: chosenDay }])}><Plus size={15} /> {t("Must visit")}</button>
              <input className="pf-min" type="number" min="1" max="1440" step="1" value={duration} onChange={(e) => setDuration(e.target.value)} placeholder={t("min")} aria-label={t("Visit duration (minutes)")} />
              <button type="button" className="btn ghost sm" style={{ width: "auto", padding: "0 12px" }}
                disabled={!Number.isInteger(Number(duration)) || Number(duration) < 1 || Number(duration) > 1440 || (!(chosen.id in durations) && Object.keys(durations).length >= 60)}
                onClick={() => setDurations({ ...durations, [chosen.id]: Number(duration) })}>{t("Set time")}</button>
            </div>
          </div>
        )}
        {(locks.length > 0 || Object.keys(durations).length > 0) && (
          <div className="chips" style={{ marginTop: 10 }}>
            {locks.map((l) => <span key={l.poi_id} className="chip blue">{nameOf(l.poi_id)} · {weekday(l.date)}{l.start_min != null && ` · ${clock(l.start_min)}`}
              <button type="button" className="chip-x" aria-label={t("Remove")} onClick={() => setLocks(locks.filter((x) => x.poi_id !== l.poi_id))}><X size={12} /></button></span>)}
            {Object.entries(durations).map(([id, m]) => <span key={id} className="chip white">{nameOf(id)} · {t("{n} min", { n: m })}
              <button type="button" className="chip-x" aria-label={t("Use default")} onClick={() => setDurations(Object.fromEntries(Object.entries(durations).filter(([k]) => k !== id)))}><X size={12} /></button></span>)}
          </div>
        )}
        <div className="row" style={{ gap: 10, marginTop: 14 }}>
          <label className="field grow">{t("Max drive per leg (min)")}<input type="number" min="0" max="1440" step="1" value={leg} placeholder={t("No limit")} onChange={(e) => setLeg(e.target.value)} /></label>
          <label className="field grow">{t("Max walking per day (min)")}<input type="number" min="0" max="1440" step="1" value={walk} placeholder={t("No limit")} onChange={(e) => setWalk(e.target.value)} /></label>
        </div>
      </details>

      <details className="pf-more">
        <summary>{t("Optional evening bars and pubs")}</summary>
        <p className="small">{t("Choose a place and day, then rebuild. Evening stops start at 18:00 or later and do not count as dinner.")}</p>
        <label className="field">{t("Search evening places")}<input maxLength={120} value={eveningSearch} onChange={(e) => setEveningSearch(e.target.value)} /></label>
        <label className="field">{t("Evening date")}<select value={eveningDay} onChange={(e) => setEveningDay(e.target.value)}>
          {dates.map((date) => <option key={date} value={date}>{fmtDate(date)}</option>)}
        </select></label>
        <ErrorBox error={evening.error ? message(evening.error) : null} />
        {evening.isLoading && <p className="small">{t("Loading evening choices…")}</p>}
        {evening.data?.length === 0 && <p className="small">{t("No matching evening places are available under your group's exclusions.")}</p>}
        {evening.data?.map((p) => {
          const selected = locks.some((l) => l.poi_id === p.id);
          return <div key={p.id} className="row" style={{ justifyContent: "space-between", margin: "8px 0" }}>
            <span className="small">{p.name}<br /><span className="muted tiny">{t(p.hours_verified ? "Saved hours available; checked when rebuilding" : "Hours unverified — confirm before visiting")}</span></span>
            <button type="button" className="chip white" disabled={selected || !eveningDay || locks.length >= 60} onClick={() => {
              setLocks([...locks, { poi_id: p.id, date: eveningDay }]);
              setNames((old) => ({ ...old, [p.id]: p.name }));
            }}>{t(selected ? "Selected" : "Include")}</button>
          </div>;
        })}
      </details>

      <details className="pf-more">
        <summary>{t("How planning works")}</summary>
        <ul className="pf-rules small">
          {view.latest_optimization?.candidate_selection && <li>{t("Reviewed {n} places and shortlisted {k} for your days.", {
            n: view.latest_optimization.candidate_selection.catalogue_count.toLocaleString(), k: view.latest_optimization.candidate_selection.selected_count })}</li>}
          <li>{t("Each day: up to {cafes} cafés and {meals} meals, and at least {acts} activities from {cats} categories.", {
            cafes: composition.max_cafes, meals: composition.max_meals, acts: composition.min_activities, cats: composition.min_activity_categories })}</li>
          <li>{t("Meal times: {windows}. A meal is added when your day covers at least {n} minutes of a meal time.", {
            windows: composition.meal_windows.filter((w) => w.name !== "breakfast").map((w) => `${t(w.name)} ${clock(w.start_min)}–${clock(w.end_min)}`).join(", "), n: composition.meal_overlap_min ?? 60 })}</li>
          <li>{t("Opening hours and closed days are checked when known; unknown hours get a warning.")}</li>
          <li>{t("Estimated visit times: cafés 45 min, meals 60 min, culture 90 min, other activities 60 min.")}</li>
          <li>{t("Driving times come from Kakao and are saved, so the same trip plans faster next time.")}</li>
          <li>{t("Everyone must confirm every trip date first. Weather and accessibility are not checked yet.")}</li>
        </ul>
      </details>

      {!view.setup.planning_data_configured && <div className="banner amber" role="status">{t("Verified operating and route data are not configured. Recommendations stay available; an itinerary cannot be verified yet.")}</div>}
      {problems.length > 0 && <div className="pf-todo small">{problems.slice(0, 3).map((p) => <div key={p}>• {p}</div>)}</div>}
      <button className="btn blue pf-build" disabled={generate.isPending || !view.setup.planning_data_configured || problems.length > 0}>
        {generate.isPending ? <><Loader size={17} className="spin" /> {t("Checking and optimizing…")}</>
          : <><Sparkles size={17} /> {view.plans.length ? t("Rebuild itinerary") : t("Build itinerary")}</>}
      </button>
      <ErrorBox error={generate.error ? message(generate.error) : null} />

      <LocationPicker tripId={tripId} open={picker !== null} title={picker === "to" ? t("Where do you finish?") : t("Where do you start?")}
        onClose={() => setPicker(null)} onPick={(p) => set(picker === "to" ? { to: p } : { from: p })} />
    </form>
  );
}
