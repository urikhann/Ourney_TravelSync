import { useQueries, useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleX, ExternalLink } from "lucide-react";
import { api } from "../api";
import { useFeatures } from "../features";
import type { Availability, GroupPick, PoiDetail } from "../types";
import { fmtDate, pct, won } from "../ui";
import PlaceThumb from "./PlaceThumb";
import Sheet from "./Sheet";
import { MemberScores } from "./Scores";

const CAT_NAME: Record<string, string> = { food: "food", cafe: "café", nature: "nature", history: "history", art: "art",
  shopping: "shopping", activity: "activity", entertainment: "entertainment", relax: "rest & walks", festival: "festival" };

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
      queryKey: ["avail", poiId, d, start],
      queryFn: () => api.get<Availability>(`/pois/${poiId}/availability?date=${d}&start=${start ?? "14:00"}`),
      enabled: !!poiId && availability,
    })),
  });
  const p = poi.data;
  return (
    <Sheet open={!!poiId} onClose={onClose} label="Place details">
      {!p ? <div className="muted center">Loading…</div> : (
        <div className="stack">
          <div className="row">
            <PlaceThumb features={p.features} image={p.image_url} size={54} />
            <div className="grow">
              <div style={{ font: "800 18px var(--display)" }}>{p.name}</div>
              <div className="muted small">{[p.area, won(p.avg_cost_krw), `~${p.duration_min} min`, p.indoor == null ? null : p.indoor ? "indoor" : "outdoor"].filter(Boolean).join(" · ")}</div>
            </div>
            {rec && <div className="score" style={{ fontSize: 18 }}>{pct(rec.fit)}</div>}
          </div>

          {rec && names && (
            <>
              <div className="h2" style={{ margin: "6px 2px 0" }}>How well it fits each person <small>group pick #{rec.group_rank}</small></div>
              <MemberScores scores={rec.member_fit} names={names} />
              <div className="chips">{rec.reasons.map((r) => <span key={r.code + r.text} className="chip green">{r.text}</span>)}</div>
            </>
          )}

          <div className="chips">{p.features.map((c) => <span key={c} className="chip white">{CAT_NAME[c] ?? c}</span>)}</div>
          {(p.address || p.phone || p.place_url) && (
            <div className="card list">
              {p.address && <div className="list-row small">{p.address}</div>}
              {p.phone && <div className="list-row small num">{p.phone}</div>}
              {p.place_url && <a className="list-row small b" href={p.place_url} target="_blank" rel="noreferrer">Open in Kakao Map <ExternalLink size={13} /></a>}
            </div>
          )}

          {!availability ? (
            <>
              <div className="h2" style={{ margin: "6px 2px 0" }}>Opening hours <small>from place data</small></div>
              <div className="card list">
                {p.hours.length === 0 && <div className="list-row muted small">Opening hours aren't in the data yet — check before you go.</div>}
                {p.hours.map((h) => (
                  <div className="list-row" key={h.day} style={{ padding: "7px 0" }}>
                    <div className="grow b" style={{ fontWeight: 600 }}>{h.day}</div>
                    <div className="muted small num">{h.open === "00:00" && h.close === "24:00" ? "Open 24h" : `${h.open}–${h.close}`}</div>
                  </div>
                ))}
              </div>
            </>
          ) : (<>
          <div className="h2" style={{ margin: "6px 2px 0" }}>
            Availability <small>{start ? `visit at ${start}` : "visit at 14:00"}</small>
          </div>
          <div className="card list">
            {dates.map((d, i) => {
              const r = checks[i]?.data;
              return (
                <div className="list-row" key={d}>
                  {r ? (r.available ? <CircleCheck size={18} color="var(--green)" /> : <CircleX size={18} color="var(--rose)" />) : <span style={{ width: 18 }} />}
                  <div className="grow">
                    <div className="b" style={{ fontWeight: 600 }}>{fmtDate(d)}</div>
                    <div className="muted tiny">
                      {!r ? "Checking…" : r.available ? `${r.close === "24:00" && r.open === "00:00" ? "Open 24h" : `Open ${r.open}–${r.close}`} · fits ${p.duration_min} min + 15 min buffer`
                        : `${r.reason}${r.next_available ? ` · next: ${fmtDate(r.next_available)}` : ""}`}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          </>)}
          <div className="muted tiny">
            Source: {p.source} · data {p.dataset_version}
            {p.field_status.duration === "ESTIMATED" ? " · visit time is an estimate" : ""}
            {p.field_status.cost === "UNKNOWN" ? " · price not known" : ""}
          </div>
        </div>
      )}
    </Sheet>
  );
}
