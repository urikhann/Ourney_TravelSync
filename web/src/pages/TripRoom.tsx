import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar, Check, Clock, Copy, Link2Off, Loader, Settings, SlidersHorizontal, Sparkles, Users } from "lucide-react";
import { api, message } from "../api";
import type { Recommendations, Trip } from "../types";
import { ErrorBox, Loading, Toast, fmtDate, useToast, weekday } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import { AvatarStack } from "../components/Avatar";
import TripSettings from "../components/TripSettings";
import { t as tr } from "../i18n";

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
  const [settings, setSettings] = useState(false);
  const trip = useQuery({ queryKey: ["trip", id], queryFn: () => api.get<Trip>(`/trips/${id}`) });

  const setFree = useMutation({
    mutationFn: (dates: string[]) => api.put<Trip>(`/trips/${id}/availability`, { dates }),
    onSuccess: (t) => qc.setQueryData(["trip", id], t),
  });
  const nudge = useMutation({
    mutationFn: () => api.post<{ nudged: string[] }>(`/trips/${id}/nudge`),
    onSuccess: (r) => toast.show(r.nudged.length ? tr("Reminder sent to {names}", { names: r.nudged.join(", ") }) : tr("Nobody to remind")),
  });
  // Recommendations precede explicit dates/endpoints/budget and E2/E3 planning.
  const generate = useMutation({
    mutationFn: async () => {
      const recs = await api.post<Recommendations>(`/trips/${id}/recommendations`, {});
      qc.setQueryData(["recs", id], recs);
      await qc.invalidateQueries({ queryKey: ["plans", id] });
    },
    onSuccess: () => nav(`/trips/${id}/plan`),
  });

  const t = trip.data;
  const days = useMemo(() => {
    if (!t) return [];
    const n = Math.round((Date.parse(t.end_date) - Date.parse(t.start_date)) / 86400000);
    return Array.from({ length: n + 5 }, (_, i) => addDays(t.start_date, i - 2));
  }, [t]);

  if (trip.isLoading) return <><TopBar title={tr("Trip room")} back="/trips" /><Loading /></>;
  if (!t) return <><TopBar title={tr("Trip room")} back="/trips" /><main className="content"><ErrorBox error={message(trip.error)} /></main></>;

  const me = t.members.find((m) => m.is_me)!;
  const tripDays = days.slice(2, days.length - 2);
  const free = (d: string) => t.members.filter((m) => m.free_dates.includes(d)).length;
  const allFree = tripDays.every((d) => free(d) === t.members.length);
  const missing = tripDays.flatMap((d) => t.members.filter((m) => !m.free_dates.includes(d)).map((m) => `${m.name} (${weekday(d)})`));
  const linkOn = (t.invite?.status ?? "active") === "active";
  const waiting = t.setup.waiting_for;
  const done = 1 + (allFree ? 1 : 0) + (waiting.length ? 0 : 1);
  const shade = (n: number) => ["#E2E8F0", "#CBD5E1", "#93C5FD", "#60A5FA", "#2563EB"][Math.round((n / t.members.length) * 4)];
  const toggle = (d: string) => setFree.mutate(me.free_dates.includes(d) ? me.free_dates.filter((x) => x !== d) : [...me.free_dates, d]);
  const copy = async () => {
    if (!linkOn) { setSettings(true); return; }
    try { await navigator.clipboard.writeText(t.invite_url); toast.show(tr("Invite link copied")); }
    catch { toast.show(t.invite_url); }
  };

  return (
    <>
      <TopBar title={tr("Trip room")} back="/trips" right={<button className="iconbtn" aria-label={tr("Trip settings")} onClick={() => setSettings(true)}><Settings size={17} /></button>} />
      <main className="content">
        <div className="hero">
          <div className="eyebrow" style={{ opacity: 0.7 }}>{tr("Group trip · {city}", { city: tr(t.city === "busan" ? "Busan" : t.city) })}</div>
          <div className="name">{t.name}</div>
          <div className="meta"><Calendar size={14} />{fmtDate(t.start_date)} – {fmtDate(t.end_date)} · {t.members.length === 1 ? tr("1 person") : tr("{n} friends", { n: t.members.length })}</div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 18 }}>
            <AvatarStack names={t.members.map((m) => m.name)} ring="#1D3A8A" />
            <button className="chip glass" onClick={copy}>{linkOn ? <><Copy size={13} /> {tr("Invite · {code}", { code: t.invite_code })}</>
              : <><Link2Off size={13} /> {t.invite?.status === "expired" ? tr("Invite link expired") : tr("Invite link off")}</>}</button>
          </div>
        </div>

        <div className="h2">{tr("Setup progress")} <small>{tr("{done} of 3 done", { done })}</small></div>
        <div className="card list">
          <SetupRow ok Icon={Users} title={tr("Members joined")} sub={tr("{n} in the trip", { n: t.members.length })}
            action={<button className="chip white" onClick={() => setSettings(true)}>{tr("Manage")}</button>} />
          <SetupRow ok={allFree} Icon={Calendar} title={tr("Dates")} sub={allFree ? tr("Every trip day works for everyone") : tr("Not free: {list}", { list: `${missing.slice(0, 3).join(", ")}${missing.length > 3 ? "…" : ""}` })} />
          <SetupRow ok={!waiting.length} Icon={SlidersHorizontal} title={tr("Preferences")}
            sub={waiting.length ? tr("Waiting for {names}", { names: waiting.join(", ") }) : tr("Everyone has answered")}
            action={waiting.length > 0 && <button className="chip blue" onClick={() => nudge.mutate()} disabled={nudge.isPending}>{tr("Nudge")}</button>} />
        </div>

        <div className="h2">{tr("Who's free?")} <small>{tr("tap a day to edit yours")}</small></div>
        <div className="card pad">
          <div className="days" style={{ gridTemplateColumns: `repeat(${days.length}, 1fr)` }}>
            {days.map((d) => {
              const mine = me.free_dates.includes(d);
              return (
                <button key={d} className={"day" + (tripDays.includes(d) ? " trip" : "")} onClick={() => toggle(d)}
                  aria-pressed={mine} aria-label={`${d}: ${tr("{n} free", { n: free(d) })}${mine ? tr(", including you") : ""}`}>
                  <div className="dow">{weekday(d)}</div>
                  <div className="dn">{new Date(d + "T00:00:00").getDate()}</div>
                  <div className="shade" style={{ background: shade(free(d)) }} />
                  <div className="mine">{mine && <Check size={12} />}</div>
                </button>
              );
            })}
          </div>
          <div className="muted tiny" style={{ marginTop: 8 }}>{tr("Bar = how many friends are free (darker = more) · ✓ = you. Confirm each trip date for the agreed daily planning window.")}</div>
        </div>

        <div style={{ height: 14 }} />
        {!me.preferences_done ? (
          <button className="btn blue" onClick={() => nav(`/trips/${id}/preferences`)}><SlidersHorizontal size={18} /> {tr("Fill in my preferences")}</button>
        ) : waiting.length ? (
          <button className="btn blue" disabled><Clock size={17} /> {tr("Waiting for {names}", { names: waiting.join(", ") })}</button>
        ) : (
          <button className="btn blue" disabled={generate.isPending} onClick={() => generate.mutate()}>
            {generate.isPending ? <><Loader size={18} className="spin" /> {tr("Finding group recommendations…")}</> : <><Sparkles size={18} /> {tr("Recommend and plan")}</>}
          </button>
        )}
        <div style={{ marginTop: 10 }}><ErrorBox error={generate.error ? message(generate.error) : null} /></div>
      </main>
      <TabBar tripId={id} />
      <TripSettings trip={t} open={settings} onClose={() => setSettings(false)} onCopy={copy} />
      <Toast text={toast.text} onDone={toast.clear} />
    </>
  );
}
