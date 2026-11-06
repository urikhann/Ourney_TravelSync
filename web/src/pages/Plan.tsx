import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CircleCheck, Clock, Lock, Map, ThumbsUp } from "lucide-react";
import { api, ApiError, message } from "../api";
import type { PlanItem, PlansView } from "../types";
import { ErrorBox, Loading, pct, weekday } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import PlaceSheet from "../components/PlaceSheet";
import OptimizationForm from "../components/OptimizationForm";

const mealLabel = (it: PlanItem) => it.place?.kind === "food" ? (it.start >= "17:00" ? "Dinner · " : "Lunch · ") : "";

export default function Plan() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<number | null>(null);
  const [day, setDay] = useState(0);
  const [openItem, setOpenItem] = useState<PlanItem | null>(null);

  const q = useQuery({ queryKey: ["plans", id], queryFn: () => api.get<PlansView>(`/trips/${id}/itineraries`) });
  const vote = useMutation({
    mutationFn: (planId: number) => api.post<PlansView>(`/itineraries/${planId}/vote`),
    onSuccess: (v) => qc.setQueryData(["plans", id], v),
  });

  const v = q.data;
  if (q.isLoading) return <><TopBar title="Choose a plan" back={`/trips/${id}`} /><Loading /><TabBar tripId={id} /></>;

  if (!v) return <><TopBar title="Itinerary" back={`/trips/${id}`} /><main className="content"><ErrorBox error={message(q.error)} /></main><TabBar tripId={id} /></>;
  const latestError = v.latest_optimization?.error_code;
  const failure = latestError ? message(new ApiError(409, latestError)) : null;
  const excluded = v.latest_optimization?.eligibility.filter(p => !p.schedule_eligible) ?? [];
  const exclusions = excluded.length > 0 && <details className="card pad" style={{ marginTop: 12 }}><summary>Excluded from automatic planning ({excluded.length})</summary>
    {excluded.map(p => <p className="small" key={p.poi_id}><b>{v.setup.points.find(x => x.id === p.poi_id)?.name ?? p.poi_id}</b><br />{p.reasons.map(r => {
      const code = r.split(":").pop() ?? r;
      const names: Record<string, string> = { COST_UNKNOWN: "Cost not verified", DURATION_UNKNOWN: "Visit duration not verified", OPERATING_INFO_UNKNOWN: "Opening or closure information not verified", ROUTE_UNAVAILABLE: "Verified route unavailable", NO_FULL_STAY_WINDOW: "Visit does not fit the available opening hours", CLOSED: "Closed", EVENT_UNVERIFIED_OR_INACTIVE: "Event date not verified", EXPLICIT_EXCLUSION: "A group exclusion applies", ACCESS_UNVERIFIED_OR_UNSATISFIED: "Required access not verified", CONSTRAINT_CONFLICT: "Group times do not overlap" };
      return names[code] ?? "Required information or conditions are not satisfied";
    }).join(" · ")}</p>)}</details>;
  if (v.plans.length === 0) {
    return (
      <>
        <TopBar title="Itinerary" back={`/trips/${id}`} />
        <main className="content">
          <div className="empty">
            <div className="ic"><Map size={30} /></div>
            <div className="title" style={{ fontSize: 18 }}>No plans yet</div>
            <p className="muted small">Plans are built from your group's recommendations.</p>
            <ErrorBox error={failure} />
            <p className="small" style={{ marginTop: 10 }}>No recommendations yet? <Link to={`/trips/${id}/places`}>Go to Places</Link></p>
          </div>
          <OptimizationForm tripId={id} view={v} />
          {exclusions}
        </main>
        <TabBar tripId={id} />
      </>
    );
  }

  // Which plan is shown: the user's pick, else the chosen plan, else my vote, else A.
  const selected = v.plans.find((p) => p.id === (tab ?? v.chosen_id ?? v.my_vote ?? v.plans[0].id)) ?? v.plans[0];
  const days = selected.days;
  const d = days[Math.min(day, days.length - 1)];
  const holiday = v.holidays.find((h) => h.date === d.date);
  const stops = selected.days.reduce((n, dd) => n + dd.items.length, 0);
  const chosen = v.plans.find((p) => p.id === v.chosen_id);

  return (
    <>
      <TopBar title="Itinerary" back={`/trips/${id}`} />
      <main className="content">
        <ErrorBox error={failure} />
        {selected.stale && <div className="banner amber">Group inputs changed. This saved itinerary belongs to an earlier snapshot; rebuild it before approval.</div>}
        <details style={{ marginBottom: 12 }}><summary>Change planning conditions</summary><OptimizationForm tripId={id} view={v} /></details>
        {exclusions}
        <p className="muted small">Verified schedule · {selected.stats.solver_status === "OPTIMAL" ? "Best result proved" : "A better plan may exist"}. Based on the saved operating and route information.</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          {v.plans.map((p) => {
            const on = p.id === selected.id;
            return (
              <button key={p.id} className={"plan-tab" + (on ? " on" : "")} onClick={() => setTab(p.id)} aria-pressed={on}>
                <div className="row tiny b" style={{ justifyContent: "space-between", opacity: on ? 0.75 : 0.6 }}>
                  PLAN {p.letter}{p.id === v.chosen_id && <span style={{ color: on ? "#6EE7B7" : "var(--green)" }}>CHOSEN</span>}
                </div>
                <div className="b" style={{ fontSize: 13, margin: "2px 0 6px", lineHeight: 1.2 }}>{p.name}</div>
                <div className="row tiny b" style={{ gap: 5, opacity: 0.85 }}><ThumbsUp size={12} />{p.votes} vote{p.votes === 1 ? "" : "s"}</div>
              </button>
            );
          })}
        </div>

        <div className="card" style={{ marginTop: 10, padding: "11px 0", display: "grid", gridTemplateColumns: "1fr 1fr 1fr" }}>
          <div className="stat"><b style={{ color: "var(--green-d)" }}>{pct(selected.stats.group_fit)}</b><span className="muted tiny">group fit</span></div>
          <div className="stat"><b>₩{selected.stats.total_cost.toLocaleString()}</b><span className="muted tiny">whole group</span></div>
          <div className="stat"><b>{stops}</b><span className="muted tiny">stops</span></div>
        </div>

        <div className="row" style={{ gap: 18, margin: "16px 4px 8px" }}>
          {days.map((dd, i) => (
            <button key={dd.date} className={"day-tab" + (i === day ? " on" : "")} onClick={() => setDay(i)}>Day {i + 1} · {weekday(dd.date)}</button>
          ))}
        </div>

        {holiday && (
          <div className="banner amber" style={{ marginBottom: 10 }}>
            <CalendarDays size={15} color="var(--amber)" />
            <span>{holiday.name} is a public holiday. Holiday closures were checked.</span>
          </div>
        )}

        <div className="tl">
          <p className="muted small">Leave {d.start_label} at {d.start}</p>
          {d.items.map((it) => {
            const moved = !!it.moved_from;
            const col = moved ? "#E0A21B" : "var(--blue)";
            return (
              <div key={it.poi_id + it.start}>
                <div className="leg"><Clock size={12} />{it.travel_minutes} min travel · arrive {it.arrival}</div>
                <div className="tl-item">
                  <div className="tl-time" style={moved ? { color: "var(--amber)" } : undefined}>{it.start}</div>
                  <div className="tl-dot" style={{ borderColor: col, background: it.locked ? col : "#fff" }} />
                  <button className={"card tl-card" + (moved ? " moved" : "")} onClick={() => setOpenItem(it)}>
                    <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                      <div className="b" style={{ fontSize: 14 }}>{mealLabel(it)}{it.place?.name ?? it.poi_id}</div>
                      {it.locked && <Lock size={15} color="var(--muted)" />}
                    </div>
                    <div className="muted tiny">{it.start}–{it.end} · {it.stay_minutes} min stay · group visit fees ₩{it.visit_cost.toLocaleString()}</div>
                    {it.locked && <div className="muted tiny" style={{ marginTop: 3 }}>Reserved · locked</div>}
                    {moved ? (
                      <span className="badge" style={{ background: "var(--amber-50)", color: "var(--amber-d)" }}>
                        <Clock size={12} /> Moved from {it.moved_from}{it.availability?.close ? ` · closes ${it.availability?.close}` : ""}
                      </span>
                    ) : it.availability?.available ? (
                      <span className="badge" style={{ background: "var(--green-50)", color: "var(--green-d)" }}>
                        <CircleCheck size={12} /> {it.availability.close === "24:00" ? "Open 24h" : it.availability.close ? `Open · closes ${it.availability.close}` : "Open"}
                      </span>
                    ) : it.availability && it.availability.available === null && (
                      <span className="badge" style={{ background: "var(--amber-50)", color: "var(--amber-d)" }}>
                        <Clock size={12} /> Hours unknown
                      </span>
                    )}
                  </button>
                </div>
              </div>
            );
          })}
          <p className="muted small">Return: {d.return_travel_minutes} min to {d.end_label} · arrive {d.end_arrival}</p>
          {d.items.length === 0 && <p className="muted small">Nothing fits this day — try rebuilding the plans.</p>}
        </div>

        <div style={{ height: 14 }} />
        <p className="muted tiny">Run #{selected.stats.optimization_run_id} · data {selected.stats.dataset_version} · routes {selected.stats.routing_version}</p>
        <ErrorBox error={vote.error ? message(vote.error) : null} />
        {selected.stale ? <p className="muted small">Approval is paused until the itinerary is rebuilt for the current group.</p> : chosen ? (
          <div className="banner green" style={{ border: "1px solid #BFE8D6" }}>
            <CircleCheck size={20} color="var(--green)" />
            <span><b>Plan {chosen.letter} chosen</b> · {chosen.votes} of {v.members} votes. It's now the trip plan.</span>
          </div>
        ) : v.my_vote === selected.id ? (
          <button className="btn ghost" disabled><ThumbsUp size={17} /> You voted for Plan {selected.letter} · {v.voted}/{v.members} voted</button>
        ) : (
          <button className="btn blue" disabled={vote.isPending} onClick={() => vote.mutate(selected.id)}>
            <ThumbsUp size={17} /> Vote for Plan {selected.letter} <span style={{ opacity: 0.7, fontWeight: 500 }}>· {v.voted}/{v.members} voted</span>
          </button>
        )}
      </main>
      <TabBar tripId={id} />
      <PlaceSheet poiId={openItem?.poi_id ?? null} onClose={() => setOpenItem(null)} dates={days.map((x) => x.date)} start={openItem?.start} />
    </>
  );
}
