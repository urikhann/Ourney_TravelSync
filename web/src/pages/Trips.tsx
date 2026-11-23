// Home screen after logging in: join a trip with an invite code, see your trips, start a new one.
import { FormEvent, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronRight, Plus, Settings, Sparkles, Ticket, Users, X } from "lucide-react";
import { api, message } from "../api";
import { LangSwitch, locale, t as tr } from "../i18n";

import { useMe } from "../auth";
import type { SavedPreferences, Tastes, Trip, TripSummary } from "../types";
import { loved } from "../components/RatingsList";
import { ErrorBox, Loading } from "../ui";
import { Avatar } from "../components/Avatar";
import TastesCard from "../components/TastesCard";

const MAX_DAYS = 3; // SRS: trips are 1–3 days
const days = (a: string, b: string) => Math.round((new Date(b + "T00:00:00").getTime() - new Date(a + "T00:00:00").getTime()) / 86_400_000) + 1;
const today = () => new Date().toLocaleDateString("sv-SE"); // "YYYY-MM-DD" in local time

// Accepts "K7PQ2MXA", "k7pq2mxa" or a whole invite link like "http://localhost:5173/join/K7PQ2MXA".
function inviteCode(text: string): string | null {
  const t = text.trim();
  const fromLink = t.match(/\/join\/([A-Za-z0-9]+)/);
  const code = (fromLink ? fromLink[1] : t).toUpperCase();
  return /^[A-Z0-9]{6,12}$/.test(code) ? code : null;
}

// "Oct 23 – 25", "Oct 31 – Nov 1" or just "Oct 23"
function range(a: string, b: string) {
  const f = (iso: string, o: Intl.DateTimeFormatOptions) => new Date(iso + "T00:00:00").toLocaleDateString(locale(), o);
  if (a === b) return f(a, { month: "short", day: "numeric" });
  if (a.slice(0, 7) === b.slice(0, 7)) return `${f(a, { month: "short", day: "numeric" })} – ${f(b, { day: "numeric" })}`;
  return `${f(a, { month: "short", day: "numeric" })} – ${f(b, { month: "short", day: "numeric" })}`;
}

function status(t: TripSummary): { label: string; cls: string } {
  const now = today();
  if (t.end_date < now) return { label: tr("Past"), cls: "past" };
  if (t.start_date <= now) return { label: tr("On the trip"), cls: "now" };
  const n = days(now, t.start_date) - 1;
  return { label: n === 1 ? tr("Tomorrow") : tr("In {n} days", { n }), cls: "soon" };
}

export default function Trips() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useMe();
  const trips = useQuery({ queryKey: ["trips"], queryFn: () => api.get<TripSummary[]>("/trips") });
  const tastes = useQuery({ queryKey: ["tastes"], queryFn: () => api.get<Tastes>("/me/tastes") });

  // ---- join with a code ----
  const [invite, setInvite] = useState("");
  const [inviteError, setInviteError] = useState<string | null>(null);
  const join = (e: FormEvent) => {
    e.preventDefault();
    const code = inviteCode(invite);
    if (!code) { setInviteError(tr("That doesn't look like an invite code. It has 8 letters and numbers, like K7PQ2MXA.")); return; }
    nav(`/join/${code}`); // the Join screen shows the trip first, then the person taps "Join this trip"
  };

  // ---- new trip ----
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", start_date: "", end_date: "", city: "busan" });
  const length = form.start_date && form.end_date ? days(form.start_date, form.end_date) : 0;
  const dateError = !form.start_date || !form.end_date ? null
    : length < 1 ? tr("The last day is before the first day.")
    : length > MAX_DAYS ? tr("Trips can be up to {n} days.", { n: MAX_DAYS }) : null;
  const create = useMutation({
    mutationFn: () => api.post<Trip>("/trips", form),
    onSuccess: (t) => { qc.invalidateQueries({ queryKey: ["trips"] }); nav(`/trips/${t.id}`); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (!dateError) create.mutate(); };

  const prefs = useQuery({ queryKey: ["my-prefs"], queryFn: () => api.get<SavedPreferences>("/me/preferences") });
  const list = [...(trips.data ?? [])].sort((a, b) => (status(a).cls === "past" ? 1 : 0) - (status(b).cls === "past" ? 1 : 0) || a.start_date.localeCompare(b.start_date));

  // Accounts that never answered (or skipped) the onboarding survey see it once.
  if (tastes.data && tastes.data.status === null) return <Navigate to="/welcome?next=/trips" replace />;

  return (
    <main className="home">
      <header className="home-head">
        <button type="button" className="home-profile" onClick={() => nav("/me")} aria-label={tr("My profile")}>
          <Avatar name={me.data?.name ?? "?"} i={0} size={40} />
          <span className="grow">
            <span className="home-hi">{me.data?.name ? tr("Hi, {name}", { name: me.data.name }) : tr("Hi there")}</span>
            <span className="muted small">{tr("Where is the group going next?")}</span>
          </span>
        </button>
        <LangSwitch compact />
        <button className="iconbtn" aria-label={tr("My profile")} title={tr("My profile")} onClick={() => nav("/me")}><Settings size={17} /></button>
      </header>

      <form className="join-card" onSubmit={join}>
        <div className="join-top">
          <span className="join-ico"><Ticket size={20} /></span>
          <div>
            <div className="join-title">{tr("Join a friend's trip")}</div>
            <div className="join-sub">{tr("Paste the invite link or type the code.")}</div>
          </div>
        </div>
        <div className="join-row">
          <input id="invite-code" aria-label={tr("Invite code or link")} placeholder="K7PQ2MXA" autoComplete="off" autoCapitalize="characters"
            value={invite} onChange={(e) => { setInvite(e.target.value); setInviteError(null); }} />
          <button className="join-btn" disabled={!invite.trim()}>{tr("Join")}</button>
        </div>
        {inviteError && <div className="join-error" role="alert">{inviteError}</div>}
      </form>

      <TastesCard />

      <div className="home-section">
        <h2>{tr("Your trips")}</h2>
        {trips.data && trips.data.length > 0 && <span className="muted small">{trips.data.length}</span>}
      </div>

      {trips.isLoading ? <Loading /> : trips.error ? <ErrorBox error={message(trips.error)} /> : list.length === 0 && !creating ? (
        <div className="home-empty">
          <p className="b">{tr("No trips yet.")}</p>
          <p className="muted small">{tr("Join one with a code above, or plan a new one below.")}</p>
        </div>
      ) : (
        <div className="trip-list">
          {list.map((t) => {
            const st = status(t), d = new Date(t.start_date + "T00:00:00");
            return (
              <Link key={t.id} to={`/trips/${t.id}`} className={"trip-card " + st.cls}>
                <div className="trip-date" aria-hidden="true">
                  <span>{d.toLocaleDateString(locale(), { month: "short" })}</span>
                  <b>{d.getDate()}</b>
                </div>
                <div className="grow">
                  <div className="trip-name">{t.name}</div>
                  <div className="trip-meta">
                    <span className={"trip-status " + st.cls}>{st.label}</span>
                    <span><CalendarDays size={13} /> {range(t.start_date, t.end_date)}</span>
                    <span><Users size={13} /> {t.member_count}</span>
                  </div>
                </div>
                <ChevronRight size={18} color="var(--soft)" />
              </Link>
            );
          })}
        </div>
      )}

      {creating ? (
        <form className="new-trip" onSubmit={submit}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div className="b" style={{ fontSize: 16 }}>{tr("Plan a new trip")}</div>
            <button type="button" className="iconbtn" aria-label={tr("Close")} onClick={() => setCreating(false)}><X size={16} /></button>
          </div>
          <label className="auth-field"><span>{tr("Trip name")}</span>
            <div className="inp"><input id="trip-name" required maxLength={80} placeholder={tr("Busan weekend")} style={{ paddingLeft: 14 }}
              value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          </label>
          <div className="row" style={{ gap: 10 }}>
            <label className="auth-field grow"><span>{tr("First day")}</span>
              <div className="inp"><input id="trip-start" type="date" required min={today()} style={{ paddingLeft: 12 }}
                value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value, end_date: form.end_date || e.target.value })} /></div>
            </label>
            <label className="auth-field grow"><span>{tr("Last day")}</span>
              <div className="inp"><input id="trip-end" type="date" required min={form.start_date || today()} style={{ paddingLeft: 12 }}
                value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></div>
            </label>
          </div>
          <p className={"auth-hint" + (dateError ? " bad" : "")}>
            {dateError ?? (length ? tr(length === 1 ? "1 day in Busan. Trips can be 1–{max} days." : "{n} days in Busan. Trips can be 1–{max} days.", { n: length, max: MAX_DAYS }) : tr("Busan, 1–{max} days.", { max: MAX_DAYS }))}
          </p>
          {prefs.data && (
            <div className="saved-note small">
              <Sparkles size={15} />
              <span className="grow">{prefs.data.saved
                ? tr("Your saved preferences are used: {list}. You can customize them for this trip later.", { list: loved(prefs.data.ratings).slice(0, 3).join(", ") || "—" })
                : tr("After creating, answer 10 quick questions once. They're saved for your next trips.")}</span>
            </div>
          )}
          <ErrorBox error={create.error ? message(create.error) : null} />
          <button className="btn blue" disabled={create.isPending || !!dateError}>{create.isPending ? tr("Creating…") : tr("Create trip")}</button>
        </form>
      ) : (
        <button className="new-trip-btn" onClick={() => setCreating(true)}><Plus size={18} /> {tr("Plan a new trip")}</button>
      )}
    </main>
  );
}
