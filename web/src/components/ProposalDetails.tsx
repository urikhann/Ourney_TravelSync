import { useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";
import type { ProposalDetails as Details, ProposalStopDetail } from "../types";
import { locale, t } from "../i18n";
import { fmtDate, reasonText } from "../ui";
import PlaceThumb from "./PlaceThumb";
import PlaceSheet from "./PlaceSheet";

const clock = (n: number) => `${Math.floor(n / 60).toString().padStart(2, "0")}:${(n % 60).toString().padStart(2, "0")}`;
const status = (s: string) => t(s === "KNOWN" ? "Verified" : s === "ESTIMATED" ? "Estimate · not verified" : "Not verified");
const change = (n: number) => n === 0 ? t("No change") : t("{n} min", { n: `${n > 0 ? "+" : "−"}${Math.abs(n)}` });

/** Inline facts for the exact proposed visit, independent of live POI fetches. */
export function ProposalPoiCard({ stop, role, version, previous }: {
  stop: ProposalStopDetail; role: "original" | "proposed" | "adjusted"; version: string;
  previous?: { start_min: number; end_min: number };
}) {
  const [openPlace, setOpenPlace] = useState(false);
  const [photoFailed, setPhotoFailed] = useState(false);
  const p = stop.place;
  const photoMissing = !p.image_url || photoFailed;
  const cost = stop.cost_per_person;
  const score = stop.recommendation_score;
  return <>
    <details className={`proposal-poi-card ${role}`}>
      <summary>
        <PlaceThumb features={p.features} image={p.image_url} size={48} onImageError={() => setPhotoFailed(true)} />
        <div className="grow">
          <div className="tiny b">{t(role === "original" ? "Original POI" : role === "proposed" ? "Proposed POI" : "Adjusted activity time")}</div>
          <div className="b">{p.name}</div>
          <div className="muted tiny">{p.category || t("Category not available")}</div>
          <div className="tiny num">{fmtDate(stop.date)} · {previous && <>{clock(previous.start_min)}–{clock(previous.end_min)} → </>}{clock(stop.start_min)}–{clock(stop.end_min)}</div>
          <div className="tiny proposal-expand-hint">{t("Expand proposal details")}</div>
        </div>
        <ChevronDown size={16} className="proposal-expand" aria-hidden="true" />
      </summary>
      <div className="proposal-poi-body">
        {photoMissing && <p className="muted tiny">{t("Photo unavailable")}</p>}
        <p className="small">{p.description || t("Description unavailable")}</p>
        {p.description_source_url && <a className="tiny" href={p.description_source_url} target="_blank" rel="noreferrer">{t("Open source")} <ExternalLink size={11} /></a>}
        <dl className="proposal-facts">
          <dt>{t("Address")}</dt><dd>{p.address || t("Address unavailable")}
            {p.map_url && <> · <a href={p.map_url} target="_blank" rel="noreferrer">{t("Open map")} <ExternalLink size={11} /></a></>}
            {p.latitude != null && p.longitude != null && <div className="muted tiny num">{p.latitude.toFixed(5)}, {p.longitude.toFixed(5)}</div>}
          </dd>
          <dt>{t("Visit duration")}</dt><dd>{t("{n} min", { n: stop.duration_minutes })} · {status(stop.duration_status)}</dd>
          <dt>{t("Opening hours")}</dt><dd>
            {stop.hours.closed === true ? t("Closed on this date") : stop.hours.windows.length ? stop.hours.windows.map(w => `${clock(w.start_min)}–${clock(w.end_min)}`).join(", ") : t("Hours unavailable")}
            <div className="muted tiny">{status(stop.hours.status)}{stop.hours.fits_visit === true ? ` · ${t("Full visit fits")}` : ""}</div>
            {stop.hours.fits_visit === false && !stop.hours.closed && <div className="tiny">{t("Visit does not fit these hours")}</div>}
            {stop.hours.checked_at && <div className="muted tiny">{t("Checked {date}", { date: new Date(stop.hours.checked_at).toLocaleString(locale(), { timeZone: "Asia/Seoul" }) })}</div>}
          </dd>
          <dt>{t("Estimated cost per person")}</dt><dd>{cost.value == null ? t("Price unknown") : `₩${cost.value.toLocaleString(locale())}`}
            <div className="muted tiny">{status(cost.status)}</div>
          </dd>
          <dt>{t("Group recommendation score")}</dt><dd>{score ? <>
            {score.value.toFixed(2)} / 1
            <div className="muted tiny">{t(score.semantics === "relative_relevance" ? "Relative relevance, not a satisfaction probability" : score.semantics === "normalized_preference" ? "Normalized group preference score" : "Score interpretation unavailable")}</div>
            <div className="muted tiny">{t("Candidate rank #{n}", { n: score.rank })}</div>
          </> : t("Score unavailable")}</dd>
        </dl>
        <div className="small b">{t("Why this place")}</div>
        {stop.recommendation_reasons.length ? <ul className="small proposal-reasons">{stop.recommendation_reasons.map((r, i) => <li key={r.code + i}>{reasonText(r.text)}</li>)}</ul>
          : <p className="muted small">{t("Recommendation reason unavailable")}</p>}
        {p.metadata_available && <button type="button" className="proposal-place" onClick={() => setOpenPlace(true)}>{t("More place information")}</button>}
      </div>
    </details>
    {openPlace && <PlaceSheet poiId={stop.poi_id} onClose={() => setOpenPlace(false)} dates={[stop.date]} start={clock(stop.start_min)} version={version} />}
  </>;
}

export default function ProposalDetails({ details }: { details: Details }) {
  return <div className="proposal-detail-list">
    {details.original.map(s => <ProposalPoiCard key={`old:${s.date}:${s.poi_id}`} stop={s} role="original" version={details.dataset_version} />)}
    {details.proposed.map(s => <ProposalPoiCard key={`new:${s.date}:${s.poi_id}`} stop={s} role="proposed" version={details.dataset_version} />)}
    {details.adjusted.map(a => <ProposalPoiCard key={`adjusted:${a.date}:${a.poi_id}`} stop={a.stop} role="adjusted" version={details.dataset_version}
      previous={{ start_min: a.before_start_min, end_min: a.before_end_min }} />)}
    <div className="proposal-impact">
      <div className="small b">{t("Impact on your itinerary")}</div>
      {details.impact ? details.impact.days.map(d => <div key={d.date} className="small">
        <div className="tiny b">{fmtDate(d.date)}</div>
        <div>{t("Travel time")}: {d.before.travel_minutes} → {d.after.travel_minutes} {t("min")} <b>({change(d.change.travel_minutes)})</b></div>
        <div>{t("Day duration")}: {d.before.itinerary_minutes} → {d.after.itinerary_minutes} {t("min")} <b>({change(d.change.itinerary_minutes)})</b></div>
      </div>) : <p className="muted small">{t("Timing impact unavailable")}</p>}
      <p className="muted tiny">{t("Compared with the saved plan. Opening checks and estimates are from the proposal snapshot.")}</p>
    </div>
  </div>;
}
