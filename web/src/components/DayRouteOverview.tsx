import { Component, lazy, Suspense, useState, type ReactNode } from "react";
import { ChevronDown, Map, Route } from "lucide-react";
import type { PlanItem } from "../types";
import { dayRoute, type PlanDay } from "../dayRoute";
import { t } from "../i18n";
import "./day-route.css";

const DayRouteMap = lazy(() => import("./DayRouteMap"));

class MapBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <p className="muted small day-route-loading" role="alert">
      {t("The map could not load. You can continue using the itinerary below.")}
    </p> : this.props.children;
  }
}

export default function DayRouteOverview({ day, onOpenItem }: { day: PlanDay; onOpenItem: (item: PlanItem) => void }) {
  const [expanded, setExpanded] = useState(false);
  const route = dayRoute(day);
  return <section className="card day-route" aria-label={t("Your day at a glance")}>
    <button type="button" className="day-route-toggle" aria-expanded={expanded} onClick={() => setExpanded(v => !v)}>
      <span className="day-route-icon"><Map size={21} aria-hidden="true" /></span>
      <span className="grow"><span className="b">{t("Your day at a glance")}</span>
        <span className="day-route-summary muted tiny">{day.items.length === 1 ? t("1 stop") : t("{n} stops", { n: day.items.length })} · {t("{n} min travel", { n: route.travelMinutes })} · {day.start}–{day.end_arrival}</span>
      </span>
      <span className="tiny b">{t(expanded ? "Hide map" : "View map")}</span>
      <ChevronDown size={15} aria-hidden="true" style={{ transform: expanded ? "rotate(180deg)" : undefined }} />
    </button>
    {expanded && <MapBoundary><Suspense fallback={<p className="muted small day-route-loading" role="status"><Route size={15} /> {t("Loading map…")}</p>}>
      <DayRouteMap key={day.date} day={day} onOpenItem={onOpenItem} />
    </Suspense></MapBoundary>}
  </section>;
}
