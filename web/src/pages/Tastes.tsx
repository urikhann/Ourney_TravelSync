// Onboarding survey ("travel tastes"): shown once after sign-up, editable later from the home screen.
// Up to 5 picks per question. It adds detail to the per-trip 1–5 survey; it does not replace it.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, X } from "lucide-react";
import { api, message } from "../api";
import type { Tastes as TastesT, TastesMeta } from "../types";
import { ErrorBox, Loading } from "../ui";
import TopBar from "../components/TopBar";

export default function Tastes() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const next = params.get("next") || "/trips";
  const editing = params.get("edit") === "1";

  const meta = useQuery({ queryKey: ["tastes-meta"], queryFn: () => api.get<TastesMeta>("/meta/tastes"), staleTime: Infinity });
  const mine = useQuery({ queryKey: ["tastes"], queryFn: () => api.get<TastesT>("/me/tastes") });

  // Local copy of the answers while editing: { food: ["seafood"], places: [...], ... }
  const [answers, setAnswers] = useState<Record<string, string[]> | null>(null);
  useEffect(() => { if (mine.data && !answers) setAnswers(mine.data.answers); }, [mine.data, answers]);

  const save = useMutation({
    mutationFn: (body: { answers?: Record<string, string[]>; skipped?: boolean }) => api.put<TastesT>("/me/tastes", body),
    onSuccess: (saved) => {
      qc.setQueryData(["tastes"], saved);
      qc.invalidateQueries({ queryKey: ["exclusions"] }); // avoid answers can turn on "never take me" rules
      nav(next, { replace: true });
    },
  });

  const title = editing ? "Your travel tastes" : "Tell us your tastes";
  const back = editing ? next : undefined;
  if (meta.isError || mine.isError) return <><TopBar title={title} back={back} /><main className="content"><ErrorBox error={message(meta.error ?? mine.error)} /></main></>;
  if (!meta.data || !answers) return <><TopBar title={title} back={back} /><Loading /></>;

  const max = meta.data.max_picks;
  const toggle = (q: string, code: string) => {
    const cur = answers[q] ?? [];
    if (cur.includes(code)) setAnswers({ ...answers, [q]: cur.filter((c) => c !== code) });
    else if (cur.length < max) setAnswers({ ...answers, [q]: [...cur, code] });
  };
  const total = Object.values(answers).reduce((n, a) => n + a.length, 0);

  return (
    <>
      <TopBar title={title} back={back} />
      <main className="content">
        <h2 className="title">What kind of traveller are you?</h2>
        <p className="muted small">Pick up to {max} in each. This helps us suggest better places from your very first trip.</p>

        {meta.data.questions.map((q) => {
          const picked = answers[q.code] ?? [];
          const avoid = q.code === "avoid";
          return (
            <section key={q.code} className="card" style={{ padding: 16, marginTop: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                <div>
                  <div className="b" style={{ fontWeight: 700 }}>{q.label_en}</div>
                  <div className="muted tiny">{q.label_ko}</div>
                </div>
                <span className={"chip " + (picked.length === max ? "amber" : "white")}>{picked.length}/{max}</span>
              </div>
              <div className="chips" style={{ marginTop: 12, gap: 8 }}>
                {q.options.map((o) => {
                  const on = picked.includes(o.code);
                  const full = !on && picked.length >= max;
                  return (
                    <button key={o.code} type="button" aria-pressed={on} disabled={full} onClick={() => toggle(q.code, o.code)}
                      className={"chip " + (on ? (avoid ? "rose" : "dark") : "dashed")}
                      style={{ padding: "8px 12px", fontSize: 13, opacity: full ? 0.45 : 1 }}>
                      {on ? (avoid ? <X size={13} /> : <Check size={13} />) : <Plus size={13} />}
                      {o.label_en} <span style={{ opacity: 0.7, fontWeight: 500 }}>{o.label_ko}</span>
                    </button>
                  );
                })}
              </div>
              {avoid && (
                <p className="muted tiny" style={{ marginTop: 10 }}>
                  Hiking, raw fish and bars are removed from your trips completely (you can change this per trip).
                  The others just count against a place.
                </p>
              )}
            </section>
          );
        })}

        <div style={{ height: 14 }} />
        <ErrorBox error={save.error ? message(save.error) : null} />
        <button className="btn" disabled={save.isPending || total === 0} onClick={() => save.mutate({ answers })}>
          {save.isPending ? "Saving…" : editing ? "Save my tastes" : "Continue"}
        </button>
        {!editing && (
          <button className="btn ghost" style={{ marginTop: 8 }} disabled={save.isPending} onClick={() => save.mutate({ skipped: true })}>
            Skip for now
          </button>
        )}
        <div style={{ height: 24 }} />
      </main>
    </>
  );
}
