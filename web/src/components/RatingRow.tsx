import type { ReactNode } from "react";

// A reusable "1 2 3 4 5" row. Used for each of the 10 taste features.
// It has NO state of its own: the parent owns the value ("controlled component").
type RatingRowProps = {
  icon: ReactNode;
  label: string;
  hint?: string;
  value: number;
  onChange: (value: number) => void;
  lowLabel?: string;
  highLabel?: string;
};

export default function RatingRow({ icon, label, hint, value, onChange, lowLabel, highLabel }: RatingRowProps) {
  return (
    <div className="list-row">
      <div className="ico-sq blue" style={{ width: 32, height: 32, borderRadius: 10 }}>{icon}</div>
      <div className="grow">
        <div className="row-label">{label}</div>
        {hint && <div className="muted tiny">{hint}</div>}
      </div>
      <div>
        <div className="rate" role="radiogroup" aria-label={label}>
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} type="button" role="radio" aria-checked={n === value}
              className={n === value ? "on" : n < value ? "lo" : ""} onClick={() => onChange(n)}>
              {n}
            </button>
          ))}
        </div>
        {(lowLabel || highLabel) && <div className="rate-ends"><span>{lowLabel}</span><span>{highLabel}</span></div>}
      </div>
    </div>
  );
}
