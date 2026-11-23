// Bottom sheet to choose a start or end location by name ("부산역", a hotel, an address) instead of
// typing latitude/longitude. Results come from GET /trips/{id}/locations (popular points, Kakao Map, our places).
// "Use my current location" asks the browser for GPS (works on https or localhost).
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, Clock, LocateFixed, MapPin, Search, Star, X } from "lucide-react";
import { api, message } from "../api";
import { t } from "../i18n";
import type { LocationHit, PlanningPoint } from "../types";
import { ErrorBox } from "../ui";
import Sheet from "./Sheet";

const RECENT = "ts_recent_locations";
export const readRecent = (): LocationHit[] => { try { return JSON.parse(localStorage.getItem(RECENT) ?? "[]"); } catch { return []; } };
const saveRecent = (h: LocationHit) => {
  try {
    const rest = readRecent().filter((x) => !(x.name === h.name && x.lat === h.lat && x.lng === h.lng));
    localStorage.setItem(RECENT, JSON.stringify([{ ...h, source: "recent" }, ...rest].slice(0, 5)));
  } catch { /* private mode: nothing remembered */ }
};

const ICON = { popular: Star, kakao: Building2, place: MapPin, mine: LocateFixed, recent: Clock };

function useDebounced(value: string, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

export default function LocationPicker({ tripId, open, title, onClose, onPick }: {
  tripId: string; open: boolean; title: string; onClose: () => void; onPick: (p: PlanningPoint) => void;
}) {
  const [q, setQ] = useState("");
  const [gps, setGps] = useState<"idle" | "busy" | "error">("idle");
  const query = useDebounced(q.trim());
  const hits = useQuery({
    queryKey: ["locations", tripId, query],
    queryFn: () => api.get<LocationHit[]>(`/trips/${tripId}/locations?q=${encodeURIComponent(query)}`),
    enabled: open, staleTime: 5 * 60_000,
  });
  const recent = query ? [] : readRecent();

  const pick = (h: LocationHit) => {
    if (h.source !== "mine") saveRecent(h);
    onPick({ label: h.name, latitude: h.lat, longitude: h.lng });
    setQ(""); onClose();
  };
  const here = () => {
    if (!navigator.geolocation) { setGps("error"); return; }
    setGps("busy");
    navigator.geolocation.getCurrentPosition(
      (pos) => { setGps("idle"); pick({ id: "mine", name: t("My location"), sub: "", lat: +pos.coords.latitude.toFixed(6), lng: +pos.coords.longitude.toFixed(6), source: "mine" }); },
      () => setGps("error"), { enableHighAccuracy: true, timeout: 10_000 });
  };
  const Row = ({ h }: { h: LocationHit }) => {
    const Icon = ICON[h.source] ?? MapPin;
    return (
      <button type="button" className="loc-row" onClick={() => pick(h)}>
        <span className={"loc-ico " + h.source}><Icon size={16} /></span>
        <span className="grow"><span className="b">{h.name}</span>{h.sub && <span className="muted tiny">{h.sub}</span>}</span>
      </button>
    );
  };

  return (
    <Sheet open={open} onClose={onClose} label={title}>
      <div className="sheet-title">{title}</div>
      <div className="loc-search">
        <Search size={17} aria-hidden="true" />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} maxLength={80}
          placeholder={t("Station, hotel, address…")} aria-label={t("Search for a place")} />
        {q && <button type="button" className="loc-clear" aria-label={t("Clear")} onClick={() => setQ("")}><X size={15} /></button>}
      </div>
      <div className="loc-list">
        <button type="button" className="loc-row" onClick={here} disabled={gps === "busy"}>
          <span className="loc-ico mine"><LocateFixed size={16} /></span>
          <span className="grow"><span className="b">{gps === "busy" ? t("Finding you…") : t("Use my current location")}</span>
            {gps === "error" && <span className="tiny" style={{ color: "var(--rose)" }}>{t("Location is blocked or unavailable. Search by name instead.")}</span>}</span>
        </button>
        {recent.length > 0 && <div className="loc-head">{t("Recent")}</div>}
        {recent.map((h) => <Row key={"r" + h.name + h.lat} h={h} />)}
        <div className="loc-head">{query ? t("Results") : t("Popular starting points")}</div>
        {hits.isLoading && <div className="muted small loc-empty">{t("Searching…")}</div>}
        {hits.data?.map((h) => <Row key={h.id} h={h} />)}
        {hits.data && hits.data.length === 0 && <div className="muted small loc-empty">{t("No match. Try the hotel name or a nearby station.")}</div>}
        <ErrorBox error={hits.error ? message(hits.error) : null} />
      </div>
    </Sheet>
  );
}
