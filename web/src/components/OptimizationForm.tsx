import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader } from "lucide-react";
import { api, message } from "../api";
import type { OptimizationInput, PlansView } from "../types";
import { ErrorBox } from "../ui";

const clock = (minute: number) => `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
const minute = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));

export default function OptimizationForm({ tripId, view }: { tripId: string; view: PlansView }) {
  const qc = useQueryClient();
  const prior = view.latest_optimization?.configuration;
  const points = view.setup.points;
  const composition = prior?.daily_composition ?? view.setup.default_composition;
  const [budget, setBudget] = useState(prior ? String(prior.budget_total) : "");
  const [mode, setMode] = useState(prior?.transport_mode ?? "");
  const [leg, setLeg] = useState(prior?.max_leg_minutes == null ? "" : String(prior.max_leg_minutes));
  const [walk, setWalk] = useState(prior?.max_total_walk_minutes == null ? "" : String(prior.max_total_walk_minutes));
  const [daily, setDaily] = useState(() => view.setup.dates.map(date => {
    const old = prior?.days.find(d => d.date === date);
    const find = (p: { latitude: number; longitude: number } | undefined) =>
      points.find(x => x.lat === p?.latitude && x.lng === p?.longitude)?.id ?? (p ? "custom" : "");
    return { date, start: old ? clock(old.start_min) : "", end: old ? clock(old.end_min) : "",
      from: find(old?.start_point), to: find(old?.end_point),
      fromLabel: old?.start_point.label ?? "", fromLat: old ? String(old.start_point.latitude) : "", fromLng: old ? String(old.start_point.longitude) : "",
      toLabel: old?.end_point.label ?? "", toLat: old ? String(old.end_point.latitude) : "", toLng: old ? String(old.end_point.longitude) : "" };
  }));
  const generate = useMutation({
    mutationFn: (body: OptimizationInput) => api.post<PlansView>(`/trips/${tripId}/itineraries/generate`, body),
    onSuccess: result => qc.setQueryData(["plans", tripId], result),
    onError: () => { void qc.invalidateQueries({ queryKey: ["plans", tripId] }); },
  });
  if (!view.setup.is_owner) return <p className="muted small">The trip owner sets the conditions and builds the itinerary.</p>;
  if (!view.setup.recommendations_ready) return <p className="muted small">First <Link to={`/trips/${tripId}/places`}>generate GFAR recommendations</Link>.</p>;
  const point = (id: string, row: typeof daily[number], field: "from" | "to") => {
    if (id === "custom") return { label: row[`${field}Label`], latitude: Number(row[`${field}Lat`]), longitude: Number(row[`${field}Lng`]) };
    const p = points.find(p => p.id === id)!;
    return { label: p.name, latitude: p.lat, longitude: p.lng };
  };
  return <form className="card pad" onSubmit={e => {
    e.preventDefault();
    generate.mutate({ budget_total: Number(budget), transport_mode: mode as OptimizationInput["transport_mode"],
      max_leg_minutes: leg === "" ? null : Number(leg), max_total_walk_minutes: walk === "" ? null : Number(walk),
      daily_composition: prior?.daily_composition,
      required_access: prior?.required_access ?? [], locked_items: prior?.locked_items ?? [],
      days: daily.map(d => ({ date: d.date, start_min: minute(d.start), end_min: d.end === "00:00" ? 1440 : minute(d.end),
        start_point: point(d.from, d, "from"), end_point: point(d.to, d, "to") })) });
  }}>
    <h2 style={{ fontSize: 17, marginTop: 0 }}>Planning conditions</h2>
    <p className="muted small">Candidates are retrieved from the full catalogue for each day using group preferences, exclusions, start/end locations, nearby clusters, and visit duration.</p>
    {view.latest_optimization?.candidate_selection && <p className="small">
      Reviewed {view.latest_optimization.candidate_selection.catalogue_count.toLocaleString()} places &middot; selected {view.latest_optimization.candidate_selection.selected_count} candidates across the trip.
    </p>}
    <p className="small">New plans: at most {composition.max_cafes} caf&eacute;s and {composition.max_meals} meals, and at least {composition.min_activities} activities from {composition.min_activity_categories} categories each day. A meal is required when that day's travel hours overlap its window by at least {composition.meal_overlap_min} minutes. {composition.separate_food_drink && "An activity separates food/drink stops."} Base meal windows: {composition.meal_windows.map(w => `${w.name} ${clock(w.start_min)}\u2013${clock(w.end_min)}`).join(", ")}. Day-specific overrides take precedence.</p>
    <p className="small">Demo routes reuse saved Kakao results without expiry. Travel times may come from an earlier request.</p>
    <p className="small">Planning test mode: opening hours, closures, and budget limits are bypassed.</p>
    <p className="muted small">Times are local. Budget covers the whole group: supplied visit fees and route fares. Every member must confirm every trip date.</p>
    {!!prior?.locked_items?.length && <p className="small">{prior.locked_items.length} locked visits will be retained.</p>}
    {!!prior?.required_access?.length && <p className="small">Required access retained: {prior.required_access.join(", ")}</p>}
    {!view.setup.planning_data_configured && <div className="banner amber" role="status">Verified operating and route data are not configured. Recommendations stay available; an itinerary cannot be verified yet.</div>}
    <label className="field">Total group budget (KRW)<input type="number" min="0" max="1000000000" step="1" required value={budget} onChange={e => setBudget(e.target.value)} /></label>
    <label className="field">Transport<select required value={mode} onChange={e => setMode(e.target.value as typeof mode)}>
      <option value="">Choose transport</option><option value="walk">Walking</option><option value="drive">Driving</option><option value="transit">Public transport</option>
    </select></label>
    {daily.map((d, i) => <fieldset key={d.date} style={{ border: "1px solid var(--line)", borderRadius: 10, margin: "12px 0", padding: 12 }}>
      <legend>{d.date}</legend>
      {(["start", "end"] as const).map(field => <label className="field" key={field}>{field === "start" ? "Leave at" : "Return by (00:00 = midnight)"}
        <input type="time" required value={d[field]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [field]: e.target.value } : row))} />
      </label>)}
      {(["from", "to"] as const).map(field => <div key={field}><label className="field">{field === "from" ? "Start location" : "End location"}
        <select required value={d[field]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [field]: e.target.value } : row))}>
          <option value="">Choose location</option><option value="custom">Enter another location</option>{points.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>{d[field] === "custom" && <>
        <label className="field">Location name<input required maxLength={120} value={d[`${field}Label`]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [`${field}Label`]: e.target.value } : row))} /></label>
        <label className="field">Latitude<input required type="number" min="-90" max="90" step="any" value={d[`${field}Lat`]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [`${field}Lat`]: e.target.value } : row))} /></label>
        <label className="field">Longitude<input required type="number" min="-180" max="180" step="any" value={d[`${field}Lng`]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [`${field}Lng`]: e.target.value } : row))} /></label>
      </>}</div>)}
    </fieldset>)}
    <label className="field">Maximum travel per leg (minutes, optional)<input type="number" min="0" max="1440" step="1" value={leg} onChange={e => setLeg(e.target.value)} /></label>
    <label className="field">Maximum walking per day (minutes, optional)<input type="number" min="0" max="1440" step="1" value={walk} onChange={e => setWalk(e.target.value)} /></label>
    <button className="btn blue" disabled={generate.isPending || !view.setup.planning_data_configured}>
      {generate.isPending ? <><Loader size={17} className="spin" /> Checking and optimizing…</> : view.plans.length ? "Rebuild itinerary" : "Build itinerary"}
    </button>
    <ErrorBox error={generate.error ? message(generate.error) : null} />
  </form>;
}
