import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar, Check, Clock, Copy, Loader, SlidersHorizontal, Sparkles, Users, Wallet } from "lucide-react";
import { api, message } from "../api";
import type { PlansView, Recommendations, Trip } from "../types";
import { ErrorBox, Loading, Toast, fmtDate, useToast, won, weekday } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import { AvatarStack } from "../components/Avatar";

const addDays = (iso: string, n: number) => {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + n);
  return d.toLocaleDateString("sv-SE"); // YYYY-MM-DD
};

function SetupRow({ ok, Icon, title, sub, action }: { ok: boolean; Icon: typeof Users; title: string; sub: string; action?: React.ReactNode }) {
  return (
    <div className="list-row">
      <div className={"ico-sq " + (ok ? "green" : "blue")}>{ok ? <Check size={17} /> : <Icon size={17} />}</div>
      <div className="grow"><div className="b" style={{ fontWeight: 600 }}>{title}</div><div className="muted small">{sub}</div></div>
      {action}
    </div>
  );
}

export default function TripRoom() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const trip = useQuery({ queryKey: ["trip", id], queryFn: () => api.get<Trip>(`/trips/${id}`) });

  const setFree = useMutation({
    mutationFn: (dates: string[]) => api.put<Trip>(`/trips/${id}/availability`, { dates }),
    onSuccess: (t) => qc.setQueryData(["trip", id], t),
  });
  const nudge = useMutation({
    mutationFn: () => api.post<{ nudged: string[] }>(`/trips/${id}/nudge`),
    onSuccess: (r) => toast.show(r.nudged.length ? `Reminder sent to ${r.nudged.join(", ")}` : "Nobody to remind"),
  });
  // One tap runs both: the recommender, then the itinerary optimizer.
  const generate = useMutation({
    mutationFn: async () => {
      const recs = await api.post<Recommendations>(`/trips/${id}/recommendations`, {});
      qc.setQueryData(["recs", id], recs);
      const plans = await api.post<PlansView>(`/trips/${id}/itineraries/generate`);
      qc.setQueryData(["plans", id], plans);
    },
    onSuccess: () => nav(`/trips/${id}/places`),
  });

  const t = trip.data;
  const days = useMemo(() => {
    if (!t) return [];
    const n = Math.round((Date.parse(t.end_date) - Date.parse(t.start_date)) / 86400000);
    return Array.from({ length: n + 5 }, (_, i) => addDays(t.start_date, i - 2));
  }, [t]);

  if (trip.isLoading) return <><TopBar title="Trip room" back="/trips" /><Loading /></>;
  if (!t) return <><TopBar title="Trip room" back="/trips" /><main className="content"><ErrorBox error={message(trip.error)} /></main></>;

  const me = t.members.find((m) => m.is_me)!;
  const tripDays = days.slice(2, days.length - 2);
  const free = (d: string) => t.members.filter((m) => m.free_dates.includes(d)).length;
  const allFree = tripDays.every((d) => free(d) === t.members.length);
  const missing = tripDays.flatMap((d) => t.members.filter((m) => !m.free_dates.includes(d)).map((m) => `${m.name} (${weekday(d)})`));
  const waiting = t.setup.waiting_for;
  const done = 1 + (allFree ? 1 : 0) + (t.budget_min != null ? 1 : 0) + (waiting.length ? 0 : 1);
  const shade = (n: number) => ["#E2E8F0", "#CBD5E1", "#93C5FD", "#60A5FA", "#2563EB"][Math.round((n / t.members.length) * 4)];
  const toggle = (d: string) => setFree.mutate(me.free_dates.includes(d) ? me.free_dates.filter((x) => x !== d) : [...me.free_dates, d]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(t.invite_url); toast.show("Invite link copied"); }
    catch { toast.show(t.invite_url); }
  };

  return (
    <>
      <TopBar title="Trip room" back="/trips" right={<button className="iconbtn" aria-label="Copy invite link" onClick={copy}><Copy size={17} /></button>} />
      <main className="content">
        <div className="hero">
          <div className="eyebrow" style={{ opacity: 0.7 }}>Group trip · {t.city}</div>
          <div className="name">{t.name}</div>
          <div className="meta"><Calendar size={14} />{fmtDate(t.start_date)} – {fmtDate(t.end_date)} · {t.members.length} {t.members.length === 1 ? "person" : "friends"}</div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 18 }}>
            <AvatarStack names={t.members.map((m) => m.name)} ring="#1D3A8A" />
            <button className="chip glass" onClick={copy}><Copy size={13} /> Invite · {t.invite_code}</button>
          </div>
        </div>

        <div className="h2">Setup progress <small>{done} of 4 done</small></div>
        <div className="card list">
          <SetupRow ok Icon={Users} title="Members joined" sub={`${t.members.length} in the trip`} />
          <SetupRow ok={allFree} Icon={Calendar} title="Dates" sub={allFree ? "Every trip day works for everyone" : `Not free: ${missing.slice(0, 3).join(", ")}${missing.length > 3 ? "…" : ""}`} />
          <SetupRow ok={t.budget_min != null} Icon={Wallet} title="Budget range"
            sub={t.budget_min != null ? `${won(t.budget_min)} – ${won(t.budget_max ?? t.budget_min)} per person` : "Not set yet"} />
          <SetupRow ok={!waiting.length} Icon={SlidersHorizontal} title="Preferences"
            sub={waiting.length ? `Waiting for ${waiting.join(", ")}` : "Everyone has answered"}
            action={waiting.length > 0 && <button className="chip blue" onClick={() => nudge.mutate()} disabled={nudge.isPending}>Nudge</button>} />
        </div>

        <div className="h2">Who's free? <small>tap a day to edit yours</small></div>
        <div className="card pad">
          <div className="days" style={{ gridTemplateColumns: `repeat(${days.length}, 1fr)` }}>
            {days.map((d) => {
              const mine = me.free_dates.includes(d);
              return (
                <button key={d} className={"day" + (tripDays.includes(d) ? " trip" : "")} onClick={() => toggle(d)}
                  aria-pressed={mine} aria-label={`${d}: ${free(d)} free${mine ? ", including you" : ""}`}>
                  <div className="dow">{weekday(d)}</div>
                  <div className="dn">{new Date(d + "T00:00:00").getDate()}</div>
                  <div className="shade" style={{ background: shade(free(d)) }} />
                  <div className="mine">{mine && <Check size={12} />}</div>
                </button>
              );
            })}
          </div>
          <div className="muted tiny" style={{ marginTop: 8 }}>Bar = how many friends are free (darker = more) · ✓ = you</div>
        </div>

        <div style={{ height: 14 }} />
        {!me.preferences_done ? (
          <button className="btn blue" onClick={() => nav(`/trips/${id}/preferences`)}><SlidersHorizontal size={18} /> Fill in my preferences</button>
        ) : waiting.length ? (
          <button className="btn blue" disabled><Clock size={17} /> Waiting for {waiting.join(", ")}</button>
        ) : (
          <button className="btn blue" disabled={generate.isPending} onClick={() => generate.mutate()}>
            {generate.isPending ? <><Loader size={18} className="spin" /> Finding a plan nobody hates…</> : <><Sparkles size={18} /> Generate itineraries</>}
          </button>
        )}
        <div style={{ marginTop: 10 }}><ErrorBox error={generate.error ? message(generate.error) : null} /></div>
      </main>
      <TabBar tripId={id} />
      <Toast text={toast.text} onDone={toast.clear} />
    </>
  );
}
