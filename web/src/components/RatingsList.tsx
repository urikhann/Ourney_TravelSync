// The 10-question taste survey (1–5 each). Shared by the profile page (my saved preferences)
// and each trip's "Me" tab (this trip's preferences).
import { CableCar, Clapperboard, Coffee, Landmark, Palette, PartyPopper, ShoppingBag, Trees, Utensils, Waves, type LucideIcon } from "lucide-react";
import { CATEGORIES, type Category } from "../types";
import { lang, t } from "../i18n";
import RatingRow from "./RatingRow";

// Record<Category, ...> forces us to describe ALL 10 features. Forget one → TypeScript error.
export const CATEGORY_INFO: Record<Category, { Icon: LucideIcon; label: string; ko: string; hint: string }> = {
  food:          { Icon: Utensils,     label: "Food & local eats",        ko: "맛집과 음식",         hint: "Restaurants, local dishes, street food" },
  cafe:          { Icon: Coffee,       label: "Cafés & desserts",         ko: "카페와 디저트",       hint: "Coffee shops, bakeries, dessert cafés" },
  nature:        { Icon: Waves,        label: "Nature & views",           ko: "자연과 경관",         hint: "Beaches, parks, mountains, viewpoints" },
  history:       { Icon: Landmark,     label: "History & tradition",      ko: "역사와 전통",         hint: "Temples, heritage sites, old villages" },
  art:           { Icon: Palette,      label: "Art, exhibitions & shows", ko: "예술과 전시 및 공연", hint: "Museums, galleries, theatres" },
  shopping:      { Icon: ShoppingBag,  label: "Shopping",                 ko: "쇼핑",               hint: "Markets, malls, shopping streets" },
  activity:      { Icon: CableCar,     label: "Activities & experiences", ko: "체험과 액티비티",     hint: "Hands-on classes, sports, walking courses" },
  entertainment: { Icon: Clapperboard, label: "Fun & entertainment",      ko: "놀이와 엔터테인먼트", hint: "Theme parks, VR, games, cinemas" },
  relax:         { Icon: Trees,        label: "Rest & walks",             ko: "휴식과 산책",         hint: "Quiet parks, promenades, spas" },
  festival:      { Icon: PartyPopper,  label: "Festivals & events",       ko: "축제와 이벤트",       hint: "Events on your travel dates" },
};

export const categoryName = (c: Category) => (lang() === "ko" ? CATEGORY_INFO[c].ko : CATEGORY_INFO[c].label);

/** "You love: …" for ratings of 4 or 5. */
export const loved = (r: Record<Category, number>) => CATEGORIES.filter((c) => r[c] >= 4).map(categoryName);

export default function RatingsList({ ratings, onChange }: { ratings: Record<Category, number>; onChange: (c: Category, v: number) => void }) {
  const ko = lang() === "ko";
  return (
    <div className="card list">
      {CATEGORIES.map((c) => {
        const { Icon, ko: k, hint } = CATEGORY_INFO[c];
        return (
          <RatingRow key={c} icon={<Icon size={16} />} label={categoryName(c)}
            hint={ko ? t(hint) : `${k} · ${hint}`} value={ratings[c]} onChange={(v) => onChange(c, v)} />
        );
      })}
    </div>
  );
}
