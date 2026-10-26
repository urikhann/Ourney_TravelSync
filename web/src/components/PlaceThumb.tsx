import { useState } from "react";
import { CableCar, Clapperboard, Coffee, Landmark, MapPin, Palette, PartyPopper, ShoppingBag, Trees, Utensils, Waves,
  type LucideIcon } from "lucide-react";

// Each of the 10 features gets an icon and a gradient. A place uses its first feature (in the fixed order).
const LOOK: Record<string, { Icon: LucideIcon; g: [string, string] }> = {
  food: { Icon: Utensils, g: ["#FDE68A", "#F59E0B"] },
  cafe: { Icon: Coffee, g: ["#F6D7B0", "#C9745A"] },
  nature: { Icon: Waves, g: ["#A5F3FC", "#0EA5E9"] },
  history: { Icon: Landmark, g: ["#C7D2FE", "#818CF8"] },
  art: { Icon: Palette, g: ["#FBCFE8", "#F472B6"] },
  shopping: { Icon: ShoppingBag, g: ["#FECDD3", "#F43F5E"] },
  activity: { Icon: CableCar, g: ["#BBF7D0", "#22C55E"] },
  entertainment: { Icon: Clapperboard, g: ["#DDD6FE", "#8B5CF6"] },
  relax: { Icon: Trees, g: ["#D9F99D", "#65A30D"] },
  festival: { Icon: PartyPopper, g: ["#FED7AA", "#F97316"] },
};
const FALLBACK = { Icon: MapPin, g: ["#CBD5E1", "#64748B"] as [string, string] };

export const placeLook = (features?: string[] | null) => LOOK[features?.[0] ?? ""] ?? FALLBACK;
export const gradient = (features?: string[] | null) => {
  const { g } = placeLook(features);
  return `linear-gradient(135deg, ${g[0]} 0%, ${g[1]} 100%)`;
};

/** The place photo when the data has one (about 12% of places), otherwise the feature icon. */
export default function PlaceThumb({ features, image, size = 50 }: { features?: string[] | null; image?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  const { Icon } = placeLook(features);
  if (image && !broken) {
    return <img className="thumb" src={image} alt="" loading="lazy" onError={() => setBroken(true)}
      style={{ width: size, height: size, objectFit: "cover" }} />;
  }
  return <div className="thumb" style={{ width: size, height: size, background: gradient(features) }}><Icon size={Math.round(size * 0.42)} /></div>;
}
