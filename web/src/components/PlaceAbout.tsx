// web/src/components/PlaceAbout.tsx  (NEW file)
// Shows the data teammate's enriched place info inside the place details sheet:
// sub-category, short description (with its source), food type / menu / activity,
// and the opening hours that were reported by the source (with how far they can be trusted).
// Missing values arrive as null from the backend and are simply not shown.
import { ExternalLink } from "lucide-react";
import type { EnrichedMeta } from "../types";

const SOURCE: Record<string, string> = {
  KAKAO_MAP: "Kakao Map", PUBLIC_TOURISM: "Public tourism data (TourAPI)", OFFICIAL_SITE: "Official website",
  LOCAL_GOV: "Local government", OTHER_RELIABLE: "Other checked source",
};

const has = (v: string | null | undefined): v is string => !!v && v.trim() !== "";

/** Sub-category, description, food type, menu, activity. Renders nothing if there is nothing to show. */
export default function PlaceAbout({ meta }: { meta?: EnrichedMeta }) {
  const m = meta ?? {};
  const facts: [string, string][] = [];
  if (has(m.food_type)) facts.push(["Food type", m.food_type]);
  if (has(m.representative_menu)) facts.push(["Signature menu", m.representative_menu]);
  if (has(m.representative_activity)) facts.push(["Things to do", m.representative_activity]);
  if (!has(m.detailed_category) && !has(m.description_summary) && facts.length === 0) return null;

  const source = has(m.description_source_type) ? SOURCE[m.description_source_type] ?? m.description_source_type : null;
  const fromMenu = m.description_basis === "STRUCTURED_MENU_FACTS" || m.description_basis === "OFFICIAL_MENU_FACTS";
  return (
    <>
      <div className="h2" style={{ margin: "6px 2px 0" }}>
        About {has(m.detailed_category) && <small>{m.detailed_category}</small>}
      </div>
      <div className="card list">
        {has(m.description_summary) && (
          <div className="list-row small" style={{ display: "block", lineHeight: 1.55 }}>
            {m.description_summary}
            {source && (
              <div className="muted tiny" style={{ marginTop: 6 }}>
                {fromMenu ? "Summary from menu info · " : ""}Source: {source}
                {has(m.description_source_url) && (
                  <> · <a href={m.description_source_url} target="_blank" rel="noreferrer" style={{ whiteSpace: "nowrap" }}>open <ExternalLink size={11} style={{ display: "inline", verticalAlign: "-1px" }} /></a></>
                )}
              </div>
            )}
          </div>
        )}
        {facts.map(([k, v]) => (
          <div className="list-row" key={k} style={{ padding: "8px 0" }}>
            <div className="grow muted small">{k}</div>
            <div className="small b" style={{ fontWeight: 600, textAlign: "right" }}>{v}</div>
          </div>
        ))}
      </div>
    </>
  );
}

const fmt = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });

/**
 * Hours reported by the source (enriched data). Rules from the data teammate:
 * - PUBLISHED_SCHEDULE: the place's regular published hours
 * - SNAPSHOT_WEEK: only seen for one week (hours_valid_from–until); must not be treated as permanent
 * - anything else: unknown, so we say so instead of guessing
 */
export function ReportedHours({ meta }: { meta?: EnrichedMeta }) {
  const m = meta ?? {};
  const hours = has(m.opening_time) && has(m.closing_time) ? `${m.opening_time}–${m.closing_time}` : null;
  const closed = has(m.closed_day) ? m.closed_day : null;
  if (!hours && !closed) return <div className="list-row muted small">Opening hours aren't known yet — check before you go.</div>;

  const snapshot = m.hours_scope === "SNAPSHOT_WEEK";
  const note = snapshot
    ? `Seen only for ${has(m.hours_valid_from) ? fmt(m.hours_valid_from) : "one week"}${has(m.hours_valid_until) ? ` – ${fmt(m.hours_valid_until)}` : ""} · may differ on your dates`
    : m.hours_scope === "PUBLISHED_SCHEDULE" ? "Published hours" : "Reported hours";
  return (
    <>
      {hours && (
        <div className="list-row" style={{ padding: "7px 0" }}>
          <div className="grow b" style={{ fontWeight: 600 }}>Open</div>
          <div className="small num">{hours}</div>
        </div>
      )}
      {closed && (
        <div className="list-row" style={{ padding: "7px 0" }}>
          <div className="grow b" style={{ fontWeight: 600 }}>Closed</div>
          <div className="small">{closed}</div>
        </div>
      )}
      <div className="list-row muted tiny" style={{ color: snapshot ? "var(--amber-d)" : undefined }}>{note}</div>
    </>
  );
}
