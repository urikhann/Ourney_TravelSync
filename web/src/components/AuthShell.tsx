// web/src/components/AuthShell.tsx  (replace the whole file)
// New look: light page, brand row, four member-colour lines that merge
// into one route (= "different tastes, one itinerary"), big title, form.
// Props are the same as before, so Login.tsx and Signup.tsx keep working.
import type { ReactNode } from "react";

type Props = {
  title?: ReactNode;
  sub?: ReactNode;
  subtitle?: ReactNode;   // alias, in case a page uses this name
  footer?: ReactNode;     // e.g. "New here? Create an account"
  children?: ReactNode;
  [key: string]: unknown; // ignore any other old props safely
};

const MEMBER = ["#2D5BFF", "#12B886", "#F59F00", "#AE3EC9"];

export function BrandMark() {
  return (
    <svg className="ts-brand-mark" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" fill="#0F1729" />
      <path
        d="M7 9.5c4 0 6 4.5 10 4.5M7 18.5c4 0 6-4.5 10-4.5h4"
        stroke="#fff"
        strokeWidth="2.2"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MergeArt() {
  // four routes (y = 14, 52, 100, 138) meet at (230, 76), then one line to the pin
  const starts = [14, 52, 100, 138];
  return (
    <svg className="ts-auth-art" viewBox="0 0 346 150" aria-hidden="true">
      <g fill="none" strokeWidth="2.5" strokeLinecap="round">
        {starts.map((y, i) => (
          <path
            key={i}
            d={`M0 ${y} C90 ${y} 140 ${y < 76 ? 74 : 78} 230 76`}
            stroke={MEMBER[i]}
          />
        ))}
        <path d="M230 76 L316 76" stroke="#0F1729" strokeWidth="4" />
      </g>
      <circle cx="230" cy="76" r="5" fill="#0F1729" />
      <circle cx="328" cy="76" r="12" fill="#0F1729" />
      <circle cx="328" cy="76" r="4" fill="#fff" />
    </svg>
  );
}

export default function AuthShell({ title, sub, subtitle, footer, children }: Props) {
  return (
    <div className="ts-auth">
      <div className="ts-auth-inner">
        <div className="ts-brand">
          <BrandMark /> TravelSync
        </div>

        <MergeArt />

        {title && <h1 className="ts-auth-title">{title}</h1>}
        {(sub ?? subtitle) && <p className="ts-auth-sub">{sub ?? subtitle}</p>}

        <div className="ts-auth-body">{children}</div>

        {footer && <div className="ts-auth-foot">{footer}</div>}
      </div>
    </div>
  );
}

// also allow:  import { AuthShell } from "../components/AuthShell";
export { AuthShell };
