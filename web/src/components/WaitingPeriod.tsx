import { Clock } from "lucide-react";
import { t } from "../i18n";

export default function WaitingPeriod({ start, end, reason }: { start: number; end: number; reason?: string }) {
  if (end <= start) return null;
  const time = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  const labels: Record<string, string> = { rest: "Rest time", fixed_lunch: "Waiting until lunch",
    fixed_dinner: "Waiting until dinner", fixed_breakfast: "Waiting until breakfast",
    opening_time: "Waiting until opening time", travel_buffer: "Travel buffer" };
  return <div className="waiting-period" role="note">
    <Clock size={15} aria-hidden="true" />
    <div><b>{t(labels[reason ?? ""] ?? "Free time")}</b>
      <div className="tiny">{time(start)}–{time(end)} · {t("{n} min", { n: end - start })}</div>
    </div>
  </div>;
}
