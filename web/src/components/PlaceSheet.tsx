import { useQueries, useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, CircleX, ExternalLink } from "lucide-react";
import { api } from "../api";
import { useFeatures } from "../features";
import type { Availability, GroupPick, PoiDetail } from "../types";
import { fmtDate, pct, reasonText, won } from "../ui";
import { t } from "../i18n";
import PlaceThumb from "./PlaceThumb";
import Sheet from "./Sheet";
import { MemberScores } from "./Scores";
import PlaceAbout, { ReportedHours } from "./PlaceAbout";

const CAT_NAME: Record<string, string> = { food: "food", cafe: "café", nature: "nature", history: "history", art: "art",
  shopping: "shopping", activity: "activity", entertainment: "entertainment", relax: "rest & walks", festival: "festival" };
const day = (d: string) => t(d);  // Mon, Tue… from the place data
/** Availability reasons from the server, in the UI language when known. */
const reasonOf = (r: string) => {
  const m = r.match(/^(Closed on public holidays) \((.+)\)$/) ?? r.match(/^(Closed): (.+)$/);
  return m ? `${t(m[1])} (${m[2]})` : t(r);
};

/** Place details: what it is, why it was picked, opening hours, and an availability check for each trip day. */
export default function PlaceSheet({ poiId, onClose, dates, start, rec, names, version }: {
  poiId: string | null; onClose: () => void; dates: string[]; start?: string;
  rec?: GroupPick; names?: Record<string, string>; version?: string;
}) {
  const { availability } = useFeatures();
  const poi = useQuery({
    queryKey: ["poi", poiId, version],
    queryFn: () => api.get<PoiDetail>(`/pois/${encodeURIComponent(poiId ?? "")}${version ? `?version=${encodeURIComponent(version)}` : ""}`),
    enabled: !!poiId,
  });
  const checks = useQueries({
    queries: dates.map((d) => ({
      queryKey: ["avail", poiId, d, start, version],
      queryFn: () => api.get<Availability>(`/pois/${encodeURIComponent(poiId ?? "")}/availability?date=${d}&start=${start ?? "14:00"}${version ? `&version=${encodeURIComponent(version)}` : ""}`),
      enabled: !!poiId && availability,
    })),
  });
  const p = poi.data;
  return (
    <Sheet open={!!poiId} onClose={onClose} label={t("Place details")}>
      {!p ? <div className="muted center">{t("Loading…")}</div> : (
        <div className="stack">
          <div className="row">
            <PlaceThumb features={p.features} image={p.image_url} size={54} />
            <div className="grow">
              <div className="sheet-title" style={{ margin: 0 }}>{p.name}</div>
              <div className="muted small">{[p.area, won(p.avg_cost_krw), "~" + t("{n} min", { n: p.duration_min }), p.indoor == null ? null : p.indoor ? t("indoor") : t("outdoor")].filter(Boolean).join(" · ")}</div>
            </div>
            {rec && <div className="score" style={{ fontSize: 18 }}>{pct(rec.fit)}</div>}
          </div>

          {rec && names && (
            <>
              <div className="h2" style={{ margin: "6px 2px 0" }}>{t("How well it fits each person")} <small>{t("group pick #{n}", { n: rec.group_rank })}</small></div>
              <MemberScores scores={rec.member_fit} names={names} />
              <div className="chips">{rec.reasons.map((r) => <span key={r.code + r.text} className="chip green">{reasonText(r.text)}</span>)}</div>
            </>
          )}

          <div className="chips">{p.features.map((c) => <span key={c} className="chip white">{t(CAT_NAME[c] ?? c)}</span>)}</div>
          <PlaceAbout meta={p.enriched_metadata} />
          {(p.address || p.phone || p.place_url) && (
            <div className="card list">
              {p.address && <div className="list-row small">{p.address}</div>}
              {p.phone && <div className="list-row small num">{p.phone}</div>}
              {p.place_url && <a className="list-row small b" href={p.place_url} target="_blank" rel="noreferrer">{t("Open in Kakao Map")} <ExternalLink size={13} /></a>}
            </div>
          )}

          {!availability ? (
            <>
              <div className="h2" style={{ margin: "6px 2px 0" }}>{t("Opening hours")} <small>{t("from place data")}</small></div>
              <div className="card list">
                {p.hours.length === 0 && <ReportedHours meta={p.enriched_metadata} />}
                {p.hours.map((h) => (
                  <div className="list-row" key={h.day} style={{ padding: "7px 0" }}>
                    <div className="grow b" style={{ fontWeight: 600 }}>{day(h.day)}</div>
                    <div className="muted small num">{!h.open ? t("Closed") : h.open === "00:00" && h.close === "24:00" ? t("Open 24h") : `${h.open}–${h.close}`}</div>
                  </div>
                ))}
                {p.hours.length > 0 && <HoursNote closedDays={p.field_status.closed_days} holidays={!!p.closed_on_public_holidays} />}
              </div>
            </>
          ) : (<>
          <div className="h2" style={{ margin: "6px 2px 0" }}>
            {t("Availability")} <small>{t("visit at {time}", { time: start ?? "14:00" })}</small>
          </div>
          <div className="card list">
            {dates.map((d, i) => {
              const r = checks[i]?.data;
              return (
                <div className="list-row" key={d}>
                  {!r ? <span style={{ width: 18 }} /> : r.available ? <CircleCheck size={18} color="var(--green)" />
                    : r.available === null ? <CircleHelp size={18} color="var(--amber)" /> : <CircleX size={18} color="var(--rose)" />}
                  <div className="grow">
                    <div className="b" style={{ fontWeight: 600 }}>{fmtDate(d)}</div>
                    <div className="muted tiny">
                      {!r ? t("Checking…") : r.available ? `${r.close === "24:00" && r.open === "00:00" ? t("Open 24h") : t("Open {from}–{to}", { from: r.open ?? "", to: r.close ?? "" })} · ${t("fits {n} min + 15 min buffer", { n: p.duration_min })}`
                        : `${reasonOf(r.reason ?? "")}${r.next_available ? ` · ${t("next: {date}", { date: fmtDate(r.next_available) })}` : ""}`}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          </>)}
          <div className="muted tiny">
            {t("Source: {src} · data {d}", { src: p.source, d: p.dataset_version })}
            {p.field_status.duration === "ESTIMATED" ? " · " + t("visit time is an estimate") : ""}
            {p.field_status.cost === "UNKNOWN" ? " · " + t("price not known") : ""}
          </div>
        </div>
      )}
    </Sheet>
  );
}

/** Under a weekly table: what we could not confirm (data rule: an unknown closed day is never "open every day"). */
function HoursNote({ closedDays, holidays }: { closedDays?: string; holidays: boolean }) {
  const notes = [
    holidays && t("Closed on public holidays."),
    closedDays === "UNKNOWN" && t("Closed days aren't confirmed — check before you go."),
    closedDays === "PARTIAL" && t("Also closed on some special days (e.g. Seollal, Chuseok) — check before you go."),
  ].filter(Boolean);
  if (!notes.length) return null;
  return <div className="list-row muted tiny" style={{ padding: "7px 0" }}>{notes.join(" ")}</div>;
}
