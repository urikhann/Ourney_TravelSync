import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  const [mode, setMode] = useState(prior?.transport_mode ?? "drive");
  const [sameTimes, setSameTimes] = useState(!prior);
  const [sameLocations, setSameLocations] = useState(!prior);
  const [locks, setLocks] = useState(prior?.locked_items ?? []);
  const [durations, setDurations] = useState<Record<string, number>>(prior?.duration_overrides ?? {});
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState("");
  const [chosenDay, setChosenDay] = useState(view.setup.dates[0] ?? "");
  const [duration, setDuration] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const searchQuery = useQuery({ queryKey: ["planning-places", tripId, search],
    queryFn: () => api.get<{ id: string; name: string; duration_min?: number }[]>(`/trips/${tripId}/planning-places?q=${encodeURIComponent(search)}`),
    enabled: view.setup.is_owner && view.setup.recommendations_ready && search.trim().length >= 2 });
  const choices = Array.from(new Map([...points.map(p => [p.id, { id: p.id, name: p.name, duration_min: undefined as number | undefined }] as const),
    ...(searchQuery.data ?? []).map(p => [p.id, p] as const)]).values());
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
  const [savedLocations, setSavedLocations] = useState<{ id: string; name: string; lat: number; lng: number }[]>([]);
  const entered = daily.flatMap(row => (["from", "to"] as const).flatMap(field => {
    const label = row[`${field}Label`].trim(), lat = row[`${field}Lat`], lng = row[`${field}Lng`];
    if (!label || lat === "" || lng === "" || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lng)) || Math.abs(Number(lat)) > 90 || Math.abs(Number(lng)) > 180) return [];
    return [{ id: `saved:${Number(lat)}:${Number(lng)}:${label}`, name: label, lat: Number(lat), lng: Number(lng) }];
  }));
  const locationChoices = Array.from(new Map([...points, ...savedLocations, ...entered].map(p => [p.id, p])).values());
  const rememberLocations = () => setSavedLocations(Array.from(new Map([...savedLocations, ...entered].map(p => [p.id, p])).values()));
  const generate = useMutation({
    mutationFn: (body: OptimizationInput) => api.post<PlansView>(`/trips/${tripId}/itineraries/generate`, body),
    onSuccess: result => qc.setQueryData(["plans", tripId], result),
    onError: () => { void qc.invalidateQueries({ queryKey: ["plans", tripId] }); },
  });
  if (!view.setup.is_owner) return <p className="muted small">The trip owner sets the conditions and builds the itinerary.</p>;
  if (!view.setup.recommendations_ready) return <p className="muted small">First <Link to={`/trips/${tripId}/places`}>generate GFAR recommendations</Link>.</p>;
  const point = (id: string, row: typeof daily[number], field: "from" | "to") => {
    if (id === "custom") return { label: row[`${field}Label`], latitude: Number(row[`${field}Lat`]), longitude: Number(row[`${field}Lng`]) };
    const p = locationChoices.find(p => p.id === id)!;
    return { label: p.name, latitude: p.lat, longitude: p.lng };
  };
  return <form className="card pad" onSubmit={e => {
    e.preventDefault();
    generate.mutate({ budget_total: 0, transport_mode: mode as OptimizationInput["transport_mode"],
      max_leg_minutes: leg === "" ? null : Number(leg), max_total_walk_minutes: walk === "" ? null : Number(walk),
      daily_composition: composition, duration_overrides: durations,
      required_access: prior?.required_access ?? [], locked_items: locks,
      days: daily.map((d, i) => {
        const timeRow = i && sameTimes ? daily[0] : d;
        const locationRow = i && sameLocations ? daily[0] : d;
        return { date: d.date, start_min: minute(timeRow.start), end_min: timeRow.end === "00:00" ? 1440 : minute(timeRow.end),
          start_point: point(locationRow.from, locationRow, "from"), end_point: point(locationRow.to, locationRow, "to") };
      }) });
  }}>
    <h2 style={{ fontSize: 17, marginTop: 0 }}>Planning conditions</h2>
    <p className="muted small">Candidates are retrieved from the full catalogue for each day using group preferences, exclusions, start/end locations, nearby clusters, and visit duration.</p>
    {view.latest_optimization?.candidate_selection && <p className="small">
      Reviewed {view.latest_optimization.candidate_selection.catalogue_count.toLocaleString()} places &middot; selected {view.latest_optimization.candidate_selection.selected_count} candidates across the trip.
    </p>}
    <p className="small">New plans: at most {composition.max_cafes} caf&eacute;s and {composition.max_meals} meals, and at least {composition.min_activities} activities from {composition.min_activity_categories} categories each day. A meal is required when that day's travel hours overlap its window by at least {composition.meal_overlap_min} minutes. {composition.separate_food_drink && "An activity separates food/drink stops."} Base meal windows: {composition.meal_windows.map(w => `${w.name} ${clock(w.start_min)}\u2013${clock(w.end_min)}`).join(", ")}. Day-specific overrides take precedence.</p>
    <p className="small">Demo routes reuse saved Kakao results without expiry. Travel times may come from an earlier request.</p>
    <p className="small">Known opening intervals and closures are respected. Unknown hours are allowed with a warning. Automatic weather handling and accessibility support are not included in this version.</p>
    <p className="small">Estimated visit times: cafés 45 min, meals 60 min, history/art/entertainment/festival 90 min, other activities 60 min. Place-specific overrides are available below.</p>
    <p className="muted small">Times are local. Every member must confirm every trip date. Missing route data stops planning; distance estimates are not substituted.</p>
    {!!prior?.locked_items?.length && <p className="small">{prior.locked_items.length} locked visits will be retained.</p>}
    {!!prior?.required_access?.length && <p className="small">Required access retained: {prior.required_access.join(", ")}</p>}
    {!view.setup.planning_data_configured && <div className="banner amber" role="status">Verified operating and route data are not configured. Recommendations stay available; an itinerary cannot be verified yet.</div>}
    <label className="field">Transport<select required value={mode} onChange={e => setMode(e.target.value as typeof mode)}>
      <option value="drive">Driving</option>{mode !== "drive" && <option value={mode} disabled>{mode} — unsupported; choose driving</option>}
    </select></label>
    {daily.length > 1 && <>
      <label className="row small"><input type="checkbox" checked={sameTimes} onChange={e => setSameTimes(e.target.checked)} />Use the first day's start and end times for every day</label>
      <label className="row small"><input type="checkbox" checked={sameLocations} onChange={e => setSameLocations(e.target.checked)} />Use the first day's start and end locations for every day</label>
      <p className="muted small">Uncheck to enter each day separately. Individual entries are kept.</p>
    </>}
    {daily.map((d, i) => <fieldset key={d.date} style={{ border: "1px solid var(--line)", borderRadius: 10, margin: "12px 0", padding: 12 }}>
      <legend>{d.date}</legend>
      {i > 0 && sameTimes ? <p className="small">Times follow the first day: {daily[0].start || "—"}–{daily[0].end || "—"}</p> : (["start", "end"] as const).map(field => <label className="field" key={field}>{field === "start" ? "Leave at" : "Return by (00:00 = midnight)"}
        <input type="time" required value={d[field]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [field]: e.target.value } : row))} />
      </label>)}
      {i > 0 && sameLocations ? <p className="small">Locations follow the first day.</p> : (["from", "to"] as const).map(field => <div key={field} onBlur={rememberLocations}><label className="field">{field === "from" ? "Start location" : "End location"}
        <select required value={d[field]} onChange={e => { rememberLocations(); setDaily(daily.map((row, j) => j === i ? { ...row, [field]: e.target.value } : row)); }}>
          <option value="">Choose location</option><option value="custom">Enter another location</option>{locationChoices.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>{d[field] === "custom" && <>
        <label className="field">Location name<input required maxLength={120} value={d[`${field}Label`]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [`${field}Label`]: e.target.value } : row))} /></label>
        <label className="field">Latitude<input required type="number" min="-90" max="90" step="any" value={d[`${field}Lat`]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [`${field}Lat`]: e.target.value } : row))} /></label>
        <label className="field">Longitude<input required type="number" min="-180" max="180" step="any" value={d[`${field}Lng`]} onChange={e => setDaily(daily.map((row, j) => j === i ? { ...row, [`${field}Lng`]: e.target.value } : row))} /></label>
      </>}</div>)}
    </fieldset>)}
    <h3>Must-visit places and visit durations</h3>
    <p className="small">Choose a recommended place or search the full catalogue. Confirmed must-visits are required on the selected day; conflicts are reported.</p>
    <label className="field">Search places<input maxLength={120} placeholder="Enter at least 2 characters" value={search} onChange={e => setSearch(e.target.value)} /></label>
    <ErrorBox error={searchQuery.error ? message(searchQuery.error) : null} />
    <label className="field">Place<select value={chosen} onChange={e => {
      const id = e.target.value, p = choices.find(p => p.id === id); setChosen(id);
      if (p) setNames(old => ({ ...old, [id]: p.name }));
      setDuration(id in durations ? String(durations[id]) : p?.duration_min == null ? "" : String(p.duration_min));
    }}><option value="">Choose a place</option>{chosen && !choices.some(p => p.id === chosen) && <option value={chosen}>{names[chosen] ?? chosen}</option>}
      {choices.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <label className="field">Visit day<select value={chosenDay} onChange={e => setChosenDay(e.target.value)}>{daily.map(d => <option key={d.date}>{d.date}</option>)}</select></label>
    <button type="button" className="btn" disabled={!chosen || locks.some(l => l.poi_id === chosen) || locks.length >= 60} onClick={() => setLocks([...locks, { poi_id: chosen, date: chosenDay }])}>Add must-visit</button>
    {locks.map(l => <p className="small" key={l.poi_id}>{names[l.poi_id] ?? choices.find(p => p.id === l.poi_id)?.name ?? l.poi_id} · {l.date}{l.start_min != null && ` · fixed ${clock(l.start_min)}`} <button type="button" onClick={() => setLocks(locks.filter(x => x.poi_id !== l.poi_id))}>Remove</button></p>)}
    <label className="field">Visit duration (minutes)<input type="number" min="1" max="1440" step="1" value={duration} onChange={e => setDuration(e.target.value)} placeholder="Use category default" /></label>
    <button type="button" className="btn" disabled={!chosen || !Number.isInteger(Number(duration)) || Number(duration) < 1 || Number(duration) > 1440 || (!(chosen in durations) && Object.keys(durations).length >= 60)} onClick={() => setDurations({ ...durations, [chosen]: Number(duration) })}>Set duration</button>
    {Object.entries(durations).map(([id, minutes]) => <p className="small" key={id}>{names[id] ?? choices.find(p => p.id === id)?.name ?? id} · {minutes} min <button type="button" onClick={() => setDurations(Object.fromEntries(Object.entries(durations).filter(([key]) => key !== id)))}>Use default</button></p>)}
    <label className="field">Maximum travel per leg (minutes, optional)<input type="number" min="0" max="1440" step="1" value={leg} onChange={e => setLeg(e.target.value)} /></label>
    <label className="field">Maximum walking per day (minutes, optional)<input type="number" min="0" max="1440" step="1" value={walk} onChange={e => setWalk(e.target.value)} /></label>
    <button className="btn blue" disabled={generate.isPending || !view.setup.planning_data_configured || mode !== "drive"}>
      {generate.isPending ? <><Loader size={17} className="spin" /> Checking and optimizing…</> : view.plans.length ? "Rebuild itinerary" : "Build itinerary"}
    </button>
    <ErrorBox error={generate.error ? message(generate.error) : null} />
  </form>;
}
