const COLORS = ["#2563EB", "#0F9F6E", "#E0892B", "#9B5DE5", "#E11D48", "#0EA5E9"];
export const colorFor = (i: number) => COLORS[i % COLORS.length];

/** A colored circle with the first letter of a name. `i` = position in the member list (picks the color). */
export function Avatar({ name, i, size = 30, ring }: { name: string; i: number; size?: number; ring?: string }) {
  return (
    <span className="av" title={name}
      style={{ background: colorFor(i), width: size, height: size, fontSize: Math.round(size * 0.4), borderColor: ring ?? (size < 26 ? "transparent" : "#fff") }}>
      {name[0]?.toUpperCase()}
    </span>
  );
}

export function AvatarStack({ names, ring }: { names: string[]; ring?: string }) {
  return <div className="avs">{names.map((n, i) => <Avatar key={i} name={n} i={i} ring={ring} />)}</div>;
}
