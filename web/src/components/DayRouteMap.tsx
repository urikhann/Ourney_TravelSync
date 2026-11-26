import { useEffect, useMemo, useRef, useState } from "react";
import * as L from "leaflet";
import { LocateFixed, MapPin } from "lucide-react";
import "leaflet/dist/leaflet.css";
import { dayRoute, type PlanDay, type RouteNode } from "../dayRoute";
import type { PlanItem } from "../types";
import { t } from "../i18n";

const samePosition = (a: RouteNode, b: RouteNode) => a.position && b.position
  && a.position[0] === b.position[0] && a.position[1] === b.position[1];

export default function DayRouteMap({ day, onOpenItem }: { day: PlanDay; onOpenItem: (item: PlanItem) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<L.Map | null>(null);
  const markers = useRef(new Map<string, L.Marker>());
  const route = useMemo(() => dayRoute(day), [day]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [background, setBackground] = useState("loading");
  const selected = route.nodes.find(n => n.key === selectedKey) ?? null;
  const fit = () => {
    const positions = route.nodes.flatMap(n => n.position ? [n.position] : []);
    if (positions.length) instance.current?.fitBounds(L.latLngBounds(positions), { padding: [35, 35], maxZoom: 16, animate: false });
  };
  useEffect(() => {
    if (!container.current) return;
    setSelectedKey(null);
    setBackground("loading");
    const map = L.map(container.current, { scrollWheelZoom: false, zoomControl: false, minZoom: 2, maxZoom: 19 });
    instance.current = map;
    map.attributionControl.setPrefix(false);
    L.control.zoom({ position: "topright", zoomInTitle: t("Zoom in"), zoomOutTitle: t("Zoom out") }).addTo(map);
    const positions = route.nodes.flatMap(n => n.position ? [n.position] : []);
    if (positions.length) map.fitBounds(L.latLngBounds(positions), { padding: [35, 35], maxZoom: 16 });
    else map.setView([0, 0], 2);
    let failed = false;
    const timer = window.setTimeout(() => setBackground("unavailable"), 8000);
    const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
      maxZoom: 19,
    });
    tiles.on("tileload", () => { window.clearTimeout(timer); if (!failed) setBackground("ready"); });
    tiles.on("tileerror", () => { failed = true; window.clearTimeout(timer); setBackground("unavailable"); });
    tiles.addTo(map);
    route.segments.forEach(segment => L.polyline(segment, { color: "#4263eb", weight: 3, dashArray: "7 7", opacity: 0.8, interactive: false }).addTo(map));
    const first = route.nodes[0], last = route.nodes[route.nodes.length - 1];
    const roundTrip = samePosition(first, last);
    for (const node of route.nodes) {
      if (!node.position || (roundTrip && node.kind === "end")) continue;
      const label = node.kind === "visit" ? String(node.number) : node.kind === "start" ? (roundTrip ? "S/E" : "S") : "E";
      const title = `${node.kind === "visit" ? t("Stop {n}", { n: node.number! }) : t(node.kind === "start" ? (roundTrip ? "Start and return" : "Start") : "Return")} · ${node.label} · ${node.time}`;
      const marker = L.marker(node.position, { title, alt: title, keyboard: true,
        zIndexOffset: node.kind === "visit" ? 200 : 0,
        icon: L.divIcon({ className: `day-route-marker ${node.kind}`, html: `<span>${label}</span>`, iconSize: [34, 34], iconAnchor: [17, 17] }),
      }).addTo(map).on("click", () => setSelectedKey(node.key));
      const tooltip = document.createElement("span");
      tooltip.textContent = title;
      marker.bindTooltip(tooltip, { direction: "top", offset: [0, -16] });
      marker.getElement()?.setAttribute("aria-label", title);
      markers.current.set(node.key, marker);
      if (roundTrip && node.kind === "start") markers.current.set("end", marker);
    }
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    observer.observe(container.current);
    return () => { observer.disconnect(); window.clearTimeout(timer); tiles.off(); markers.current.clear(); map.remove(); instance.current = null; };
  }, [route]);

  const choose = (node: RouteNode) => {
    setSelectedKey(node.key);
    if (node.position) {
      instance.current?.panTo(node.position);
      markers.current.get(node.key)?.openTooltip();
    }
  };
  const hasPositions = route.nodes.some(n => n.position);
  return <div className="day-route-body">
    <div className="day-route-toolbar">
      <p className="muted tiny">{t("Tap a numbered stop to explore the day.")}</p>
      <button type="button" className="day-route-fit tiny b" onClick={fit} disabled={!hasPositions}><LocateFixed size={15} /> {t("Fit all stops")}</button>
    </div>
    {hasPositions ? <div className="day-route-canvas" ref={container} role="region" aria-label={t("Day route map")} />
      : <p className="day-route-empty muted small">{t("Map positions are unavailable for this day. You can still explore every stop below.")}</p>}
    {hasPositions && background === "unavailable" && <p className="muted tiny" role="status">{t("Map background unavailable. Stop markers and visit order still work.")}</p>}
    {selected && <div className="day-route-selected" role="status">
      <div className="small b">{selected.kind === "visit" ? `${selected.number}. ` : `${t(selected.kind === "start" ? "Start" : "Return")} · `}{selected.label}</div>
      <div className="tiny muted">{selected.time}{selected.itemIndex != null && day.items[selected.itemIndex].place?.category ? ` · ${day.items[selected.itemIndex].place?.category}` : ""}</div>
      {selected.itemIndex != null && <button type="button" className="day-route-details small b" onClick={() => onOpenItem(day.items[selected.itemIndex!])}>{t("View place details")}</button>}
    </div>}
    <p className="day-route-note tiny muted">{t("Dashed lines show visit order, not road directions. Travel times come from the saved itinerary.")}</p>
    {route.missingVisits > 0 && <p className="tiny muted">{route.missingVisits === 1 ? t("1 stop has no map position; its place details remain available below.") : t("{n} stops have no map position; their place details remain available below.", { n: route.missingVisits })}</p>}
    {route.missingEndpoints > 0 && <p className="tiny muted">{t("Some start or return positions are unavailable in this saved plan.")}</p>}
    <div className="day-route-stops" role="group" aria-label={t("Stops in visit order")}>
      {route.nodes.map(node => <button type="button" key={node.key} className={`day-route-stop ${node.kind}${selectedKey === node.key ? " selected" : ""}`}
        aria-pressed={selectedKey === node.key} onClick={() => choose(node)}>
        <span className="day-route-number">{node.kind === "visit" ? node.number : node.kind === "start" ? "S" : "E"}</span>
        <span className="grow"><span className="tiny muted">{node.kind === "visit" ? node.time : `${t(node.kind === "start" ? "Start" : "Return")} · ${node.time}`}</span><span className="small b">{node.label}</span></span>
        {!node.position && <MapPin size={13} aria-label={t("Map position unavailable")} />}
      </button>)}
    </div>
  </div>;
}
