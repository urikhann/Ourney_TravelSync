// Home screen after logging in: join a trip with an invite code, see your trips, start a new one.
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronRight, LogOut, Plus, Ticket, Users, X } from "lucide-react";
import { api, message, setToken } from "../api";
import { useMe } from "../auth";
import type { Trip, TripSummary } from "../types";
import { ErrorBox, Loading } from "../ui";
import { Avatar } from "../components/Avatar";

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
  const f = (iso: string, o: Intl.DateTimeFormatOptions) => new Date(iso + "T00:00:00").toLocaleDateString("en-US", o);
  if (a === b) return f(a, { month: "short", day: "numeric" });
  if (a.slice(0, 7) === b.slice(0, 7)) return `${f(a, { month: "short", day: "numeric" })} – ${f(b, { day: "numeric" })}`;
  return `${f(a, { month: "short", day: "numeric" })} – ${f(b, { month: "short", day: "numeric" })}`;
}

function status(t: TripSummary): { label: string; cls: string } {
  const now = today();
  if (t.end_date < now) return { label: "Past", cls: "past" };
  if (t.start_date <= now) return { label: "On the trip", cls: "now" };
  const n = days(now, t.start_date) - 1;
  return { label: n === 1 ? "Tomorrow" : `In ${n} days`, cls: "soon" };
}

export default function Trips() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useMe();
  const trips = useQuery({ queryKey: ["trips"], queryFn: () => api.get<TripSummary[]>("/trips") });

  // ---- join with a code ----
  const [invite, setInvite] = useState("");
  const [inviteError, setInviteError] = useState<string | null>(null);
  const join = (e: FormEvent) => {
    e.preventDefault();
    const code = inviteCode(invite);
    if (!code) { setInviteError("That doesn't look like an invite code. It has 8 letters and numbers, like K7PQ2MXA."); return; }
    nav(`/join/${code}`); // the Join screen shows the trip first, then the person taps "Join this trip"
  };

  // ---- new trip ----
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", start_date: "", end_date: "", city: "busan" });
  const length = form.start_date && form.end_date ? days(form.start_date, form.end_date) : 0;
  const dateError = !form.start_date || !form.end_date ? null
    : length < 1 ? "The last day is before the first day."
    : length > MAX_DAYS ? `Trips can be up to ${MAX_DAYS} days.` : null;
  const create = useMutation({
    mutationFn: () => api.post<Trip>("/trips", form),
    onSuccess: (t) => { qc.invalidateQueries({ queryKey: ["trips"] }); nav(`/trips/${t.id}`); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (!dateError) create.mutate(); };

  const logout = () => { setToken(null); qc.clear(); nav("/login"); };
  const list = [...(trips.data ?? [])].sort((a, b) => (status(a).cls === "past" ? 1 : 0) - (status(b).cls === "past" ? 1 : 0) || a.start_date.localeCompare(b.start_date));

  return (
    <main className="home">
      <header className="home-head">
        <Avatar name={me.data?.name ?? "?"} i={0} size={40} />
        <div className="grow">
          <div className="home-hi">Hi, {me.data?.name ?? "there"}</div>
          <div className="muted small">Where is the group going next?</div>
        </div>
        <button className="iconbtn" aria-label="Log out" title="Log out" onClick={logout}><LogOut size={17} /></button>
      </header>

      <form className="join-card" onSubmit={join}>
        <div className="join-top">
          <span className="join-ico"><Ticket size={20} /></span>
          <div>
            <div className="join-title">Join a friend's trip</div>
            <div className="join-sub">Paste the invite link or type the code.</div>
          </div>
        </div>
        <div className="join-row">
          <input id="invite-code" aria-label="Invite code or link" placeholder="K7PQ2MXA" autoComplete="off" autoCapitalize="characters"
            value={invite} onChange={(e) => { setInvite(e.target.value); setInviteError(null); }} />
          <button className="join-btn" disabled={!invite.trim()}>Join</button>
        </div>
        {inviteError && <div className="join-error" role="alert">{inviteError}</div>}
      </form>

      <div className="home-section">
        <h2>Your trips</h2>
        {trips.data && trips.data.length > 0 && <span className="muted small">{trips.data.length}</span>}
      </div>

      {trips.isLoading ? <Loading /> : trips.error ? <ErrorBox error={message(trips.error)} /> : list.length === 0 && !creating ? (
        <div className="home-empty">
          <p className="b">No trips yet.</p>
          <p className="muted small">Join one with a code above, or plan a new one below.</p>
        </div>
      ) : (
        <div className="trip-list">
          {list.map((t) => {
            const st = status(t), d = new Date(t.start_date + "T00:00:00");
            return (
              <Link key={t.id} to={`/trips/${t.id}`} className={"trip-card " + st.cls}>
                <div className="trip-date" aria-hidden="true">
                  <span>{d.toLocaleDateString("en-US", { month: "short" })}</span>
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
            <div className="b" style={{ fontSize: 16 }}>Plan a new trip</div>
            <button type="button" className="iconbtn" aria-label="Close" onClick={() => setCreating(false)}><X size={16} /></button>
          </div>
          <label className="auth-field"><span>Trip name</span>
            <div className="inp"><input id="trip-name" required maxLength={80} placeholder="Busan weekend" style={{ paddingLeft: 14 }}
              value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          </label>
          <div className="row" style={{ gap: 10 }}>
            <label className="auth-field grow"><span>First day</span>
              <div className="inp"><input id="trip-start" type="date" required min={today()} style={{ paddingLeft: 12 }}
                value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value, end_date: form.end_date || e.target.value })} /></div>
            </label>
            <label className="auth-field grow"><span>Last day</span>
              <div className="inp"><input id="trip-end" type="date" required min={form.start_date || today()} style={{ paddingLeft: 12 }}
                value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></div>
            </label>
          </div>
          <p className={"auth-hint" + (dateError ? " bad" : "")}>
            {dateError ?? (length ? `${length} ${length === 1 ? "day" : "days"} in Busan. Trips can be 1–${MAX_DAYS} days.` : `Busan, 1–${MAX_DAYS} days.`)}
          </p>
          <ErrorBox error={create.error ? message(create.error) : null} />
          <button className="btn blue" disabled={create.isPending || !!dateError}>{create.isPending ? "Creating…" : "Create trip"}</button>
        </form>
      ) : (
        <button className="new-trip-btn" onClick={() => setCreating(true)}><Plus size={18} /> Plan a new trip</button>
      )}
    </main>
  );
}
