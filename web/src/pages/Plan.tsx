import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CircleCheck, Clock, Lock, LockOpen, Map, ThumbsUp } from "lucide-react";
import { api, ApiError, message } from "../api";
import type { DailyComposition, PlanItem, PlansView } from "../types";
import { isBreakfastVisit } from "../planLabels";
import { ErrorBox, Loading, pct, weekday } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import PlaceSheet from "../components/PlaceSheet";
import OptimizationForm from "../components/OptimizationForm";
import PlanningInfo from "../components/PlanningInfo";
import WaitingPeriod from "../components/WaitingPeriod";
import DayRouteOverview from "../components/DayRouteOverview";
import { t } from "../i18n";

const mealLabel = (it: PlanItem, date: string, policy?: DailyComposition | null) => {
  if (isBreakfastVisit(it, date, policy)) return t("Breakfast") + " · ";
  if (it.visit_category === "evening") return t("Evening") + " · ";
  if (it.visit_category === "meal" || (!it.visit_category && it.place?.kind === "food")) {
    return t(it.start >= "17:00" ? "Dinner" : "Lunch") + " · ";
  }
  return "";
};

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
  const placeLock = useMutation({
    mutationFn: ({ planId, itemId, locked, revision }: { planId: number; itemId: string; locked: boolean; revision: number }) =>
      api.post<PlansView>(`/trips/${id}/itineraries/${planId}/lock`, { item_id: itemId, locked, expected_revision: revision }),
    onSuccess: (view) => qc.setQueryData(["plans", id], view),
    onError: () => { void qc.invalidateQueries({ queryKey: ["plans", id] }); },
  });

  const v = q.data;
  if (q.isLoading) return <><TopBar title={t("Choose a plan")} back={`/trips/${id}`} /><Loading /><TabBar tripId={id} /></>;

  if (!v) return <><TopBar title={t("Itinerary")} back={`/trips/${id}`} /><main className="content"><ErrorBox error={message(q.error)} /></main><TabBar tripId={id} /></>;
  const latestError = v.latest_optimization?.error_code;
  const failure = latestError ? message(new ApiError(409, latestError)) : null;
  /* Hidden on the timeline to reduce clutter; retain eligibility diagnostics.
  const excluded = v.latest_optimization?.eligibility.filter(p => !p.schedule_eligible) ?? [];
  const exclusions = excluded.length > 0 && <details className="card pad" style={{ marginTop: 12 }}><summary>{t("Excluded from automatic planning ({n})", { n: excluded.length })}</summary>
    {excluded.map(p => <p className="small" key={p.poi_id}><b>{v.setup.points.find(x => x.id === p.poi_id)?.name ?? p.poi_id}</b><br />{p.reasons.map(r => {
      const code = r.split(":").pop() ?? r;
      const names: Record<string, string> = { COST_UNKNOWN: "Cost not verified", DURATION_UNKNOWN: "Visit duration not verified", OPERATING_INFO_UNKNOWN: "Opening or closure information not verified", OPENING_HOURS_UNVERIFIED: "Opening hours need confirmation", ROUTE_UNAVAILABLE: "No usable route within travel limits", DAY_CANDIDATE_NOT_SELECTED: "Not shortlisted for this day", NO_FULL_STAY_WINDOW: "Visit does not fit the available time", CLOSED: "Closed", EVENT_UNVERIFIED_OR_INACTIVE: "Event date not verified", EXPLICIT_EXCLUSION: "A group exclusion applies", ACCESS_UNVERIFIED_OR_UNSATISFIED: "Required access not verified", CONSTRAINT_CONFLICT: "Group times do not overlap" };
      return `${r.includes(":") ? r.slice(0, r.lastIndexOf(":")) + ": " : ""}${t(names[code] ?? "Required information or conditions are not satisfied")}`;
    }).join(" · ")}</p>)}</details>;
  */
  if (v.plans.length === 0) {
    return (
      <>
        <TopBar title={t("Itinerary")} back={`/trips/${id}`} right={<PlanningInfo view={v} />} />
        <main className="content">
          <div className="empty">
            <div className="ic"><Map size={30} /></div>
            <div className="title" style={{ fontSize: 18 }}>{t("No plans yet")}</div>
            <p className="muted small">{t("Plans use the full place catalogue and your group's preferences.")}</p>
            <ErrorBox error={failure} />
            <p className="small" style={{ marginTop: 10 }}>{t("No recommendations yet?")} <Link to={`/trips/${id}/places`}>{t("Go to Places")}</Link></p>
          </div>
          <OptimizationForm tripId={id} view={v} />
          {/* {exclusions} */}
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
      <TopBar title={t("Itinerary")} back={`/trips/${id}`} right={<PlanningInfo view={v} />} />
      <main className="content">
        <ErrorBox error={failure} />
        {!!selected.stats.routing_warnings?.length && <div className="banner amber" role="status">{t("The routing allowance or provider quota was reached. This plan uses available saved routes; some alternatives could not be checked.")}</div>}
        {/* <div className="banner amber">{t(selected.stats.opening_hours_enforced ? "Known opening intervals and closures were checked. Visits with unknown hours need confirmation." : "This older plan did not enforce opening hours. Rebuild it to apply the current rules.")} {t("Weather and accessibility are not checked.")}</div> */}
        {selected.stale && <div className="banner amber">{t("Group inputs changed. This saved itinerary belongs to an earlier snapshot; rebuild it before approval.")}</div>}
        <details className="pf-more" style={{ marginBottom: 12 }}><summary>{t("Change planning conditions")}</summary><OptimizationForm key={JSON.stringify(v.setup.configuration?.locked_items)} tripId={id} view={v} /></details>
        {/* {exclusions} */}
        <p className="muted small">{t("Schedule checked against saved routes, available hours and estimated visit durations")} · {t(selected.stats.solver_status === "OPTIMAL" ? "Best result proved for the configured objectives" : "A better plan may exist")}.</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          {v.plans.map((p) => {
            const on = p.id === selected.id;
            return (
              <button key={p.id} className={"plan-tab" + (on ? " on" : "")} onClick={() => setTab(p.id)} aria-pressed={on}>
                <div className="row tiny b" style={{ justifyContent: "space-between", opacity: on ? 0.75 : 0.6 }}>
                  {t("PLAN {x}", { x: p.letter })}{p.id === v.chosen_id && <span style={{ color: on ? "#6EE7B7" : "var(--green)" }}>{t("CHOSEN")}</span>}
                </div>
                <div className="b" style={{ fontSize: 13, margin: "2px 0 6px", lineHeight: 1.2 }}>{t(p.name)}</div>
                <div className="row tiny b" style={{ gap: 5, opacity: 0.85 }}><ThumbsUp size={12} />{p.votes === 1 ? t("1 vote") : t("{n} votes", { n: p.votes })}</div>
              </button>
            );
          })}
        </div>

        <div className="card" style={{ marginTop: 10, padding: "11px 0", display: "grid", gridTemplateColumns: "1fr 1fr" }}>
          <div className="stat"><b style={{ color: "var(--green-d)" }}>{pct(selected.stats.group_fit)}</b><span className="muted tiny">{t("group fit")}</span></div>
          <div className="stat"><b>{stops}</b><span className="muted tiny">{t("stops")}</span></div>
        </div>

        <div className="row" style={{ gap: 18, margin: "16px 4px 8px" }}>
          {days.map((dd, i) => (
            <button key={dd.date} className={"day-tab" + (i === day ? " on" : "")} onClick={() => setDay(i)}>{t("Day {n}", { n: i + 1 })} · {weekday(dd.date)}</button>
          ))}
        </div>

        {holiday && (
          <div className="banner amber" style={{ marginBottom: 10 }}>
            <CalendarDays size={15} color="var(--amber)" />
            <span>{t("{name} is a public holiday. Confirm holiday hours where information is missing.", { name: holiday.name })}</span>
          </div>
        )}

        <DayRouteOverview key={selected.id} day={d} onOpenItem={setOpenItem} />

        {d.items.every(it => it.visit_category) && <p className="muted small">
          {t("{m} meals · {c} cafés · {a} activities across {k} categories", { m: d.items.filter(it => it.visit_category === "meal").length,
            c: d.items.filter(it => it.visit_category === "cafe").length, a: d.items.filter(it => it.visit_category === "activity").length,
            k: new Set(d.items.filter(it => it.visit_category === "activity").map(it => it.activity_category)).size })}
        </p>}
        {d.breakfast_omitted && !d.items.some(it => isBreakfastVisit(it, d.date, selected.stats.daily_composition)) && <p className="muted small" role="status">{t("Breakfast was skipped because the planner could not fit a suitable stop into this day.")}</p>}
        {d.evening_omitted && <p className="muted small" role="status">{t("An evening bar or pub was skipped because the planner could not fit a suitable stop into this day.")}</p>}
        <div className="tl">
          {v.setup.is_owner && <p className="muted small">{t("Tap the dot beside a place to lock or unlock it.")}</p>}
          <ErrorBox error={placeLock.error ? message(placeLock.error) : null} />
          <p className="muted small">{t("Leave {place} at {time}", { place: d.start_label, time: d.start })}</p>
          {d.items.map((it, index) => {
            const moved = !!it.moved_from;
            const col = moved ? "#E0A21B" : "var(--blue)";
            const gap = d.gaps?.find(g => g.before_poi_id === it.poi_id);
            const minutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
            const gapStart = gap?.start_min ?? it.arrival_min ?? minutes(it.arrival);
            const gapEnd = gap?.end_min ?? it.start_min ?? minutes(it.start);
            return (
              <div key={it.poi_id + it.start}>
                <div className="leg"><Clock size={12} />{t("{n} min travel · arrive {time}", { n: it.travel_minutes, time: it.arrival })}</div>
                {index > 0 && <WaitingPeriod start={gapStart} end={gapEnd} reason={gap?.reason} />}
                {/* Hours and serving-time notices hidden on the timeline.
                {it.breakfast_status && <p className="muted small">{t(it.breakfast_status === "verified"
                  ? "Breakfast serving hours verified."
                  : "Likely suitable for breakfast. Confirm serving times before visiting.")}</p>}
                {it.availability?.available !== true && <p className="small" role="status">{t("Opening hours unverified — confirm before visiting.")}</p>}
                */}
                <div className="tl-item">
                  <div className="tl-time" style={moved ? { color: "var(--amber)" } : undefined}>{it.start}</div>
                  <button type="button" className="tl-dot tl-lock" aria-pressed={it.locked}
                    aria-label={t(it.locked ? "Unlock {place}" : "Lock {place}", { place: it.place?.name ?? it.poi_id })}
                    title={t(it.completed ? "Completed activities stay protected" : it.lock_reason === "confirmed_reservation"
                      ? "Confirmed reservations stay protected" : !v.setup.is_owner ? "Only the trip owner can change place locks"
                      : it.locked ? "Tap to unlock" : "Tap to lock")}
                    disabled={!v.setup.is_owner || it.completed || it.lock_reason === "confirmed_reservation" || placeLock.isPending}
                    style={{ borderColor: col, background: it.locked ? col : "#fff", color: it.locked ? "#fff" : col }}
                    onClick={() => placeLock.mutate({ planId: selected.id, itemId: `${d.date}@${it.start}`,
                      locked: !it.locked, revision: selected.lock_revision })}>
                    {it.locked ? <Lock size={11} aria-hidden="true" /> : <LockOpen size={11} aria-hidden="true" />}
                  </button>
                  <button className={"card tl-card" + (moved ? " moved" : "")} onClick={() => setOpenItem(it)}>
                    <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                      <div className="b" style={{ fontSize: 14 }}>{mealLabel(it, d.date, selected.stats.daily_composition)}{it.place?.name ?? it.poi_id}</div>
                      {it.locked && <Lock size={15} color="var(--muted)" />}
                    </div>
                    <div className="muted tiny">{it.start}–{it.end} · {t("{n} min stay", { n: it.stay_minutes })}</div>
                    {it.locked && <div className="muted tiny" style={{ marginTop: 3 }}>{t(it.lock_reason === "confirmed_reservation" ? "Confirmed reservation" : it.lock_reason === "legacy_lock_unverified" ? "Protected activity · lock origin unverified" : "Explicitly locked activity")}</div>}
                    {it.completed && <div className="muted tiny">{t("Completed or already started")}</div>}
                    {moved && (
                      <span className="badge" style={{ background: "var(--amber-50)", color: "var(--amber-d)" }}>
                        <Clock size={12} /> {t("Moved from {time}", { time: it.moved_from ?? "" })}
                      </span>
                    )}
                    {it.availability?.available === true && (
                      <span className="badge" style={{ background: "var(--green-50)", color: "var(--green-d)" }}>
                        <CircleCheck size={12} /> {it.availability.close === "24:00" ? t("Open 24h") : it.availability.close ? t("Open ? closes {time}", { time: it.availability.close }) : t("Open")}
                      </span>
                    )}
                    {/* {it.availability && it.availability.available === null && (
                      <span className="badge" style={{ background: "var(--amber-50)", color: "var(--amber-d)" }}>
                        <Clock size={12} /> {t("Hours unknown")}
                      </span>
                    )} */}
                  </button>
                </div>
              </div>
            );
          })}
          <p className="muted small">{t("Return: {n} min to {place} · arrive {time}", { n: d.return_travel_minutes, place: d.end_label, time: d.end_arrival })}</p>
          {d.items.length === 0 && <p className="muted small">{t("Nothing fits this day — try rebuilding the plans.")}</p>}
        </div>

        <div style={{ height: 14 }} />
        <p className="muted tiny">{t("Run #{id} · data {data} · routes {routes}", { id: selected.stats.optimization_run_id, data: selected.stats.dataset_version, routes: selected.stats.routing_version })}</p>
        <ErrorBox error={vote.error ? message(vote.error) : null} />
        {selected.stale ? <p className="muted small">{t("Approval is paused until the itinerary is rebuilt for the current group.")}</p> : chosen ? (
          <div className="banner green" style={{ border: "1px solid #BFE8D6" }}>
            <CircleCheck size={20} color="var(--green)" />
            <span><b>{t("Plan {x} chosen", { x: chosen.letter })}</b> · {t("{a} of {n} votes. It's now the trip plan.", { a: chosen.votes, n: v.members })}</span>
          </div>
        ) : v.my_vote === selected.id ? (
          <button className="btn ghost" disabled><ThumbsUp size={17} /> {t("You voted for Plan {x}", { x: selected.letter })} · {t("{a}/{n} voted", { a: v.voted, n: v.members })}</button>
        ) : (
          <button className="btn blue" disabled={vote.isPending} onClick={() => vote.mutate(selected.id)}>
            <ThumbsUp size={17} /> {t("Vote for Plan {x}", { x: selected.letter })} <span style={{ opacity: 0.7, fontWeight: 500 }}>· {t("{a}/{n} voted", { a: v.voted, n: v.members })}</span>
          </button>
        )}
      </main>
      <TabBar tripId={id} />
      <PlaceSheet poiId={openItem?.poi_id ?? null} onClose={() => setOpenItem(null)} dates={days.map((x) => x.date)} start={openItem?.start} version={selected.stats.dataset_version} />
    </>
  );
}
