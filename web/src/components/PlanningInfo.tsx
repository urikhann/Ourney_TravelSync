import { useState } from "react";
import { Info, X } from "lucide-react";
import type { PlansView } from "../types";
import { t } from "../i18n";
import Sheet from "./Sheet";

const clock = (minute: number) => `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;

export default function PlanningInfo({ view }: { view: PlansView }) {
  const [open, setOpen] = useState(false);
  const prior = view.setup.configuration ?? view.latest_optimization?.configuration;
  const composition = prior?.daily_composition ?? view.setup.default_composition;
  return <>
    <button type="button" className="iconbtn" aria-label={t("How planning works")} title={t("How planning works")}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}><Info size={20} /></button>
    <Sheet open={open} onClose={() => setOpen(false)} label={t("How planning works")}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 className="sheet-title">{t("How planning works")}</h2>
        <button type="button" className="iconbtn" aria-label={t("Close")} onClick={() => setOpen(false)}><X size={20} /></button>
      </div>
      <p className="small">{t("The planner prioritizes group preference fit, then the least-served member's fit, then shorter travel and less waiting, while keeping required constraints.")}</p>
      <ul className="pf-rules small">
        {view.latest_optimization?.candidate_selection && <li>{t("Reviewed {n} places and shortlisted {k} for your days.", {
          n: view.latest_optimization.candidate_selection.catalogue_count.toLocaleString(), k: view.latest_optimization.candidate_selection.selected_count })}</li>}
        <li>{t("Each day: up to {cafes} cafés and {meals} meals, and at least {acts} activities from {cats} categories.", {
          cafes: composition.max_cafes, meals: composition.max_meals, acts: composition.min_activities, cats: composition.min_activity_categories })}</li>
        <li>{t("Meal times: {windows}. A meal is added when your day covers at least {n} minutes of a meal time.", {
          windows: composition.meal_windows.filter(w => w.name !== "breakfast").map(w => `${t(w.name)} ${clock(w.start_min)}–${clock(w.end_min)}`).join(", "), n: composition.meal_overlap_min ?? 60 })}</li>
        <li>{t("Opening hours and closed days are checked when known; unknown hours get a warning.")}</li>
        <li>{t("Estimated visit times: cafés 45 min, meals 60 min, culture 90 min, other activities 60 min.")}</li>
        <li>{t("Driving times come from Kakao and are saved, so the same trip plans faster next time.")}</li>
        <li>{t("Everyone must confirm every trip date first. Weather and accessibility are not checked yet.")}</li>
        <li>{t("Optional breakfast and evening stops follow your settings. Rain and less-walking requests prioritize their requested reduction. A better plan may exist within the search limits.")}</li>
      </ul>
    </Sheet>
  </>;
}
