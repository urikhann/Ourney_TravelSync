// "Your travel tastes": a summary of the onboarding survey answers with an Edit button.
// Shown on the home screen and in each trip's "Me" tab, so tastes can be changed any time after sign-up.
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Heart, Pencil } from "lucide-react";
import { api } from "../api";
import type { Tastes, TastesMeta } from "../types";

export default function TastesCard({ compact = false }: { compact?: boolean }) {
  const nav = useNavigate();
  const here = useLocation().pathname;
  const mine = useQuery({ queryKey: ["tastes"], queryFn: () => api.get<Tastes>("/me/tastes") });
  const meta = useQuery({ queryKey: ["tastes-meta"], queryFn: () => api.get<TastesMeta>("/meta/tastes"), staleTime: Infinity });
  if (!mine.data || !meta.data) return null;

  const edit = () => nav(`/welcome?edit=1&next=${encodeURIComponent(here)}`);
  const label: Record<string, string> = {};
  meta.data.questions.forEach((q) => q.options.forEach((o) => { label[`${q.code}.${o.code}`] = o.label_en; }));
  const a = mine.data.answers;
  const likes = ["food", "places", "activities"].flatMap((q) => (a[q] ?? []).map((c) => label[`${q}.${c}`] ?? c));
  const avoid = (a.avoid ?? []).map((c) => label[`avoid.${c}`] ?? c);
  const empty = mine.data.status !== "done" || likes.length + avoid.length === 0;
  const shown = likes.slice(0, compact ? 4 : 6);

  return (
    <section className="card" style={{ padding: 14, marginTop: compact ? 16 : 14, marginBottom: compact ? 0 : 4 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div className="row" style={{ gap: 10, alignItems: "center" }}>
          <span className="chip blue" style={{ padding: 7, borderRadius: 12 }}><Heart size={15} /></span>
          <div>
            <div className="b" style={{ fontWeight: 700 }}>Your travel tastes</div>
            <div className="muted tiny">{empty ? "Not set yet — add them for better picks" : "Used for all your trips"}</div>
          </div>
        </div>
        <button className="btn ghost sm" style={{ width: "auto", padding: "0 14px" }} onClick={edit}>
          <Pencil size={14} /> {empty ? "Add" : "Edit"}
        </button>
      </div>
      {!empty && (
        <div className="chips" style={{ marginTop: 10 }}>
          {shown.map((l) => <span key={l} className="chip white">{l}</span>)}
          {likes.length > shown.length && <span className="chip white">+{likes.length - shown.length}</span>}
          {avoid.map((l) => <span key={l} className="chip rose">No {l.toLowerCase()}</span>)}
        </div>
      )}
    </section>
  );
}
