import { useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, ShieldCheck, SlidersHorizontal, Sparkles } from "lucide-react";
import { ApiError, api, message } from "../api";
import type { GroupPick, Method, Recommendations, Trip } from "../types";
import { ErrorBox, Loading, pct, reasonText, won } from "../ui";
import { t as tr } from "../i18n";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import PlaceThumb, { gradient, placeLook } from "../components/PlaceThumb";
import { MemberScores, ScoreRing } from "../components/Scores";
import PlaceSheet from "../components/PlaceSheet";
import Sheet from "../components/Sheet";

type Filter = "all" | "food" | "culture" | "nature";
const FILTERS: { key: Filter; label: string; has: string[] }[] = [
  { key: "all", label: "All", has: [] },
  { key: "food", label: "Food & cafés", has: ["food", "cafe"] },
  { key: "culture", label: "Culture", has: ["history", "art", "festival"] },
  { key: "nature", label: "Nature", has: ["nature", "relax"] },
];
const METHODS: { key: Method; label: string }[] = [
  { key: "gfar", label: "Fair (GFAR)" }, { key: "awm", label: "No misery (AWM)" }, { key: "average", label: "Average" },
];

const tripDates = (t?: Trip) => {
  if (!t) return [];
  const out: string[] = [];
  for (let d = new Date(t.start_date + "T00:00:00"); d <= new Date(t.end_date + "T00:00:00"); d.setDate(d.getDate() + 1)) out.push(d.toLocaleDateString("sv-SE"));
  return out;
};

export default function Places() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [openPoi, setOpenPoi] = useState<string | null>(null);
  const [showRemoved, setShowRemoved] = useState(false);

  const trip = useQuery({ queryKey: ["trip", id], queryFn: () => api.get<Trip>(`/trips/${id}`) });
  const recs = useQuery({
    queryKey: ["recs", id],
    queryFn: () => api.get<Recommendations>(`/trips/${id}/recommendations/latest`),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 1,
  });
  const rerun = useMutation({
    mutationFn: (method: Method) => api.post<Recommendations>(`/trips/${id}/recommendations`, { method }),
    onSuccess: (r) => qc.setQueryData(["recs", id], r),
  });

  const r = recs.data;
  const none = recs.error instanceof ApiError && recs.error.status === 404;
  const f = FILTERS.find((x) => x.key === filter)!;
  const list: GroupPick[] = (r?.group ?? []).filter((x) => filter === "all" || x.place?.features.some((c) => f.has.includes(c)));
  const open = r?.group.find((x) => x.poi_id === openPoi);
  const right = <button className="iconbtn" aria-label={tr("Why places were removed")} onClick={() => setShowRemoved(true)} disabled={!r}><SlidersHorizontal size={17} /></button>;
  const nameOf = (s: string) => s.replace(/[0-9a-f-]{36}|u_[\w-]+/g, (m) => r?.members[m] ?? m);

  return (
    <>
      <TopBar title={tr("Picked for your group")} back={`/trips/${id}`} right={right} />
      <main className="content">
        {recs.isLoading ? <Loading /> : none || !r ? (
          <div className="empty">
            <div className="ic"><Sparkles size={30} /></div>
            <div className="title" style={{ fontSize: 18 }}>{tr("No recommendations yet")}</div>
            <p className="muted small">{tr("Once everyone has answered, run the recommender.")}</p>
            <button className="btn blue" onClick={() => rerun.mutate("gfar")} disabled={rerun.isPending}>{tr("Get recommendations")}</button>
            <div style={{ marginTop: 10 }}><ErrorBox error={rerun.error ? message(rerun.error) : null} /></div>
          </div>
        ) : (
          <>
            <div className="banner green" style={{ marginBottom: 10 }}>
              <ShieldCheck size={20} color="var(--green)" />
              <div className="grow">
                {r.method === "gfar"
                  ? <><b>{tr("Fair for everyone.")}</b> {tr("Each pick goes to whoever is least served so far.")}</>
                  : r.method === "awm"
                    ? <><b>{tr("No one unhappy.")}</b> {tr("Places anyone would score too low are removed, then the best average wins.")}</>
                    : <><b>{tr("Plain average.")}</b> {tr("For comparison: the most popular places, no turn-taking.")}</>}
              </div>
            </div>
            <div className="seg" style={{ gridTemplateColumns: `repeat(${METHODS.length}, 1fr)`, marginBottom: 10 }}>
              {METHODS.map((m) => (
                <button key={m.key} type="button" className={r.method === m.key ? "on" : ""} disabled={rerun.isPending}
                  onClick={() => r.method !== m.key && rerun.mutate(m.key)}>{tr(m.label)}</button>
              ))}
            </div>
            <ErrorBox error={rerun.error ? message(rerun.error) : null} />

            <div className="chips" style={{ marginBottom: 12 }}>
              {FILTERS.map((x) => (
                <button key={x.key} className={"chip " + (filter === x.key ? "dark" : "white")} onClick={() => setFilter(x.key)}>
                  {tr(x.label)}{x.key === "all" ? ` ${r.group.length}` : ""}
                </button>
              ))}
            </div>

            {list.length === 0 && <p className="muted small center">{tr("No places in this filter.")}</p>}
            <div className="stack" style={{ gap: 10 }}>
              {list.map((x, i) => {
                const p = x.place;
                if (i === 0) {
                  const { Icon } = placeLook(p?.features);
                  return (
                    <button key={x.poi_id} className="card place-hero" onClick={() => setOpenPoi(x.poi_id)}>
                      <div className="cover" style={{ background: p?.image_url ? `center / cover url("${p.image_url}"), ${gradient(p?.features)}` : gradient(p?.features) }}>
                        <span className="chip"><Icon size={13} /> {p?.area}</span>
                        <span className="lbl">{tr("#{n} GROUP PICK", { n: x.group_rank })}</span>
                        <ScoreRing score={x.fit} />
                      </div>
                      <div className="body">
                        <div className="pname">{p?.name ?? x.poi_id}</div>
                        <div className="muted small" style={{ marginTop: 2 }}>{p ? `${won(p.avg_cost_krw)} · ~${tr("{n} min", { n: p.duration_min })}${p.indoor ? " · " + tr("indoor") : ""}` : ""}</div>
                        <div style={{ margin: "12px 0" }}><MemberScores scores={x.member_fit} names={r.members} /></div>
                        <div className="eyebrow muted" style={{ marginBottom: 7 }}>{tr("Why this place")}</div>
                        <div className="chips">{x.reasons.map((re) => <span key={re.code + re.text} className="chip green">{reasonText(re.text)}</span>)}</div>
                      </div>
                    </button>
                  );
                }
                return (
                  <button key={x.poi_id} className="card row" style={{ padding: "11px 12px", width: "100%", textAlign: "left" }} onClick={() => setOpenPoi(x.poi_id)}>
                    <PlaceThumb features={p?.features} image={p?.image_url} size={50} />
                    <div className="grow">
                      <div className="b" style={{ fontSize: 14 }}>{p?.name ?? x.poi_id}</div>
                      <div className="muted tiny" style={{ marginTop: 2 }}>{x.reasons.slice(0, 2).map((re) => reasonText(re.text)).join(" · ")}</div>
                    </div>
                    <div className="score" style={{ fontSize: 13 }}>{pct(x.fit)}</div>
                  </button>
                );
              })}
            </div>

            <button className="row muted small" style={{ gap: 6, margin: "14px 2px 0" }} onClick={() => setShowRemoved(true)}>
              <Info size={14} /> {tr("{n} places removed — see why", { n: r.removed.length })}
            </button>
            <p className="muted tiny center" style={{ marginTop: 12 }}>
              {tr("Model {m} · data {d} · run #{id}", { m: r.model_version, d: r.dataset_version, id: r.run_id })}
            </p>
          </>
        )}
      </main>
      <TabBar tripId={id} />

      <PlaceSheet poiId={openPoi} onClose={() => setOpenPoi(null)} dates={tripDates(trip.data)} version={r?.dataset_version}
        rec={open} names={r?.members} />

      <Sheet open={showRemoved} onClose={() => setShowRemoved(false)} label={tr("Removed places")}>
        <div className="sheet-title">{tr("Why places were removed")}</div>
        <p className="muted small">{tr("Before scoring, places that break someone's \"never take me to\" rule are removed (step E1). With AWM, places someone would score too low are removed too.")}</p>
        <div className="card list">
          {r?.removed.length === 0 && <div className="list-row muted small">{tr("Nothing was removed.")}</div>}
          {r?.removed.map((x) => (
            <div className="list-row" key={x.poi_id}>
              <PlaceThumb features={x.place?.features} size={34} />
              <div className="grow">
                <div className="b" style={{ fontWeight: 600 }}>{x.place?.name ?? x.poi_id}</div>
                <div className="tiny" style={{ color: "var(--rose)" }}>
                  {tr(x.rule === "misery" ? "Someone would score it too low (AWM)" : x.rule === "no_features" ? "No taste features in the data" : "Personal rule")} · {nameOf(x.detail)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Sheet>
    </>
  );
}
