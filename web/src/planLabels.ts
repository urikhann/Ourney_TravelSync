import type { DailyComposition, PlanItem } from "./types";

const minutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));

/** A timeline label only: fitting the breakfast window does not verify a menu. */
export function isBreakfastVisit(item: PlanItem, date: string, policy?: DailyComposition | null): boolean {
  if (item.breakfast_status) return true;
  if (item.visit_category !== "cafe" && (item.visit_category || item.place?.kind !== "cafe")) return false;
  const base = policy?.meal_windows.find(w => w.name === "breakfast") ?? { start_min: 420, end_min: 600 };
  const override = policy?.meal_overrides?.find(o => o.date === date && o.name === "breakfast");
  let { start_min: start, end_min: end } = override?.window ?? base;
  const target = override?.fixed_start_min ?? override?.preferred_start_min;
  if (!override?.window && target != null && !(start <= target && target < end)) {
    end = Math.min(1440, target + end - start);
    start = target;
  }
  const arrival = item.start_min ?? minutes(item.start);
  const departure = minutes(item.end);
  return start <= arrival && arrival < departure && departure <= end
    && (override?.fixed_start_min == null || arrival === override.fixed_start_min);
}
