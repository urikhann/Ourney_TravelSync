import { Avatar } from "./Avatar";

/** Circular group fit: the mean of everyone's 0–1 score, shown as a percentage. */
export function ScoreRing({ score, bad }: { score: number; bad?: boolean }) {
  const pct = Math.max(0, Math.min(100, score * 100));
  const col = bad ? "#E11D48" : "#0F9F6E";
  return (
    <div className="ring" aria-label={`Group fit ${Math.round(pct)} percent`}>
      <div style={{ background: `conic-gradient(${col} 0 ${pct}%, #E2E8F0 0 100%)` }}>
        <div style={{ color: bad ? "var(--rose)" : undefined, fontSize: 13 }}>{Math.round(pct)}%</div>
      </div>
    </div>
  );
}

/** Each member's 0–1 fit as a small bar (SDD: P(u,i) = sum of weight × feature). Optional AWM threshold turns bars red. */
export function MemberScores({ scores, names, threshold }: { scores: Record<string, number>; names: Record<string, string>; threshold?: number | null }) {
  const ids = Object.keys(scores);
  const max = Math.max(0.5, ...ids.map((u) => scores[u]));  // scale bars so small differences are visible
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(4, ids.length)}, 1fr)`, gap: 8 }}>
      {ids.map((u, i) => {
        const s = scores[u], bad = threshold != null && s < threshold;
        return (
          <div key={u} className="row" style={{ gap: 6 }} title={names[u]}>
            <Avatar name={names[u] ?? "?"} i={i} size={22} />
            <div className="grow">
              <div className="bar"><i className={bad ? "bad" : ""} style={{ width: `${(s / max) * 100}%` }} /></div>
              <div className="tiny b num" style={{ marginTop: 2, color: bad ? "var(--rose)" : undefined }}>{Math.round(s * 100)}%</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
