// Trip settings sheet (issue #18): the invite link, who is in the trip, leaving and deleting.
// Everyone can copy the link and leave. Only the owner can renew or turn off the link,
// remove members and delete the trip. Every destructive step asks once more inside the sheet.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, Crown, Link2, Link2Off, LogOut, RefreshCw, Trash2, UserMinus } from "lucide-react";
import { api, message } from "../api";
import { locale, t } from "../i18n";
import type { Trip } from "../types";
import { ErrorBox } from "../ui";
import Sheet from "./Sheet";
import { Avatar } from "./Avatar";

const until = (iso: string) => new Date(iso).toLocaleDateString(locale(), { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

type Confirm = { kind: "leave" } | { kind: "delete" } | { kind: "remove"; userId: string; name: string } | null;

export default function TripSettings({ trip, open, onClose, onCopy }: { trip: Trip; open: boolean; onClose: () => void; onCopy: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [days, setDays] = useState(7);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const status = trip.invite?.status ?? "active";
  const owner = trip.is_owner;
  const others = trip.members.filter((m) => !m.is_me);

  const update = (t2: Trip) => qc.setQueryData(["trip", trip.id], t2);
  const renew = useMutation({ mutationFn: () => api.post<Trip>(`/trips/${trip.id}/invite`, { days }), onSuccess: update });
  const revoke = useMutation({ mutationFn: () => api.del<Trip>(`/trips/${trip.id}/invite`), onSuccess: update });
  const remove = useMutation({
    mutationFn: (userId: string) => api.del<Trip>(`/trips/${trip.id}/members/${userId}`),
    onSuccess: (t2) => { update(t2); setConfirm(null); },
  });
  const gone = () => { qc.invalidateQueries({ queryKey: ["trips"] }); qc.removeQueries({ queryKey: ["trip", trip.id] }); nav("/trips", { replace: true }); };
  const leave = useMutation({ mutationFn: () => api.post(`/trips/${trip.id}/leave`), onSuccess: gone });
  const del = useMutation({ mutationFn: () => api.del(`/trips/${trip.id}`), onSuccess: gone });
  const error = [renew, revoke, remove, leave, del].find((m) => m.error)?.error;

  return (
    <Sheet open={open} onClose={() => { setConfirm(null); onClose(); }} label={t("Trip settings")}>
      <div className="sheet-title">{t("Trip settings")}</div>

      {/* ---- invite link ---- */}
      <div className="h2" style={{ margin: "14px 2px 8px" }}>{t("Invite link")}</div>
      <div className="card pad invite-box">
        <div className="row" style={{ gap: 12 }}>
          <span className={"ico-sq " + (status === "active" ? "green" : "rose")}>{status === "active" ? <Link2 size={18} /> : <Link2Off size={18} />}</span>
          <div className="grow">
            <div className="b num" style={{ letterSpacing: ".08em" }}>{status === "active" ? trip.invite_code : "— — — —"}</div>
            <div className="muted tiny">
              {status === "active" ? t("Works until {date}", { date: until(trip.invite!.expires_at) })
                : status === "expired" ? t("Expired. Make a new link to invite more people.")
                : t("Turned off. Nobody can join with an old link.")}
            </div>
          </div>
          {status === "active" && <button className="btn ghost sm" style={{ width: "auto", padding: "0 12px" }} onClick={onCopy}><Copy size={14} /> {t("Copy")}</button>}
        </div>
        {owner && (
          <div className="invite-owner">
            <div className="muted tiny" style={{ marginBottom: 6 }}>{t("A new link replaces the old one. It works for:")}</div>
            <div className="seg" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              {[1, 3, 7].map((d) => <button key={d} type="button" className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d === 1 ? t("1 day") : t("{n} days", { n: d })}</button>)}
            </div>
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <button className="btn sm grow" disabled={renew.isPending} onClick={() => renew.mutate()}><RefreshCw size={15} /> {t("New link")}</button>
              {status !== "revoked" && <button className="btn ghost sm grow" disabled={revoke.isPending} onClick={() => revoke.mutate()}><Link2Off size={15} /> {t("Turn off")}</button>}
            </div>
          </div>
        )}
      </div>

      {/* ---- members ---- */}
      <div className="h2" style={{ margin: "18px 2px 8px" }}>{t("Members")} <small>{trip.members.length}</small></div>
      <div className="card list" style={{ padding: "0 16px" }}>
        {trip.members.map((m, i) => (
          <div className="list-row" key={m.user_id}>
            <Avatar name={m.name} i={i} size={32} />
            <div className="grow">
              <div className="b" style={{ fontWeight: 600 }}>{m.name}{m.is_me && <span className="muted"> · {t("you")}</span>}</div>
              {m.role === "owner" && <div className="tiny" style={{ color: "var(--amber-d)" }}><Crown size={11} style={{ verticalAlign: "-1px" }} /> {t("Owner")}</div>}
            </div>
            {owner && !m.is_me && (confirm?.kind === "remove" && confirm.userId === m.user_id ? (
              <button className="chip rose" disabled={remove.isPending} onClick={() => remove.mutate(m.user_id)}>{t("Remove {name}?", { name: m.name })}</button>
            ) : (
              <button className="iconbtn" aria-label={t("Remove {name}", { name: m.name })} onClick={() => setConfirm({ kind: "remove", userId: m.user_id, name: m.name })}><UserMinus size={16} /></button>
            ))}
          </div>
        ))}
      </div>

      {/* ---- leave / delete ---- */}
      <div className="stack" style={{ marginTop: 18, gap: 8 }}>
        {confirm?.kind === "leave" ? (
          <div className="confirm-box">
            <div className="b">{t("Leave this trip?")}</div>
            <p className="muted small">{others.length === 0 ? t("You're the only member, so the trip will be deleted.")
              : owner ? t("Your dates and ratings are removed. {name} becomes the owner.", { name: others[0].name })
              : t("Your dates and ratings are removed. You can join again with a new invite link.")}</p>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn ghost sm grow" onClick={() => setConfirm(null)}>{t("Cancel")}</button>
              <button className="btn sm grow danger" disabled={leave.isPending} onClick={() => leave.mutate()}>{t("Leave")}</button>
            </div>
          </div>
        ) : confirm?.kind === "delete" ? (
          <div className="confirm-box">
            <div className="b">{t("Delete “{name}” for everyone?", { name: trip.name })}</div>
            <p className="muted small">{t("The plans, chat and everyone's answers are deleted. This can't be undone.")}</p>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn ghost sm grow" onClick={() => setConfirm(null)}>{t("Cancel")}</button>
              <button className="btn sm grow danger" disabled={del.isPending} onClick={() => del.mutate()}>{t("Delete trip")}</button>
            </div>
          </div>
        ) : (
          <>
            <button className="btn ghost sm" onClick={() => setConfirm({ kind: "leave" })}><LogOut size={15} /> {t("Leave trip")}</button>
            {owner && <button className="btn ghost sm danger-text" onClick={() => setConfirm({ kind: "delete" })}><Trash2 size={15} /> {t("Delete trip")}</button>}
          </>
        )}
      </div>
      <div style={{ marginTop: 10 }}><ErrorBox error={error ? message(error) : null} /></div>
    </Sheet>
  );
}
