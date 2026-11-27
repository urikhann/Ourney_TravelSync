import type { Plan } from "./types";

export type PlanDay = Plan["days"][number];
export type Position = [number, number];
export type RouteNode = {
  key: string; kind: "start" | "visit" | "end"; label: string; time: string;
  position: Position | null; itemIndex?: number; number?: number;
};

// Leaflet's street map uses Web Mercator; missing data must never become (0, 0).
export function position(lat: unknown, lng: unknown): Position | null {
  return typeof lat === "number" && typeof lng === "number" && Number.isFinite(lat) && Number.isFinite(lng)
    && Math.abs(lat) <= 85.051129 && Math.abs(lng) <= 180 ? [lat, lng] : null;
}

export function dayRoute(day: PlanDay) {
  const start = day.map_endpoints?.start;
  const end = day.map_endpoints?.end;
  const nodes: RouteNode[] = [
    { key: "start", kind: "start", label: start?.label || day.start_label, time: day.start,
      position: position(start?.latitude, start?.longitude) },
    ...day.items.map((item, i): RouteNode => ({ key: `visit:${i}`, kind: "visit", number: i + 1, itemIndex: i,
      label: item.place?.name ?? item.poi_id, time: `${item.start}–${item.end}`,
      position: position(item.place?.lat, item.place?.lng) })),
    { key: "end", kind: "end", label: end?.label || day.end_label, time: day.end_arrival,
      position: position(end?.latitude, end?.longitude) },
  ];
  // Do not draw a shortcut across a stop whose location is missing.
  const segments = nodes.slice(1).flatMap((node, i) => {
    const previous = nodes[i];
    return previous.position && node.position ? [[previous.position, node.position]] : [];
  });
  return { nodes, segments,
    travelMinutes: day.items.reduce((sum, item) => sum + item.travel_minutes, day.return_travel_minutes),
    missingVisits: nodes.filter(n => n.kind === "visit" && !n.position).length,
    missingEndpoints: nodes.filter(n => n.kind !== "visit" && !n.position).length };
}
