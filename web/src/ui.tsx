import { useCallback, useEffect, useState } from "react";
import { CircleCheck } from "lucide-react";

export function ErrorBox({ error }: { error: string | null | undefined }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export function Loading() {
  return <div className="empty muted">Loading…</div>;
}

export function Toast({ text, onDone }: { text: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!text) return;
    const t = setTimeout(onDone, 2400);
    return () => clearTimeout(t);
  }, [text, onDone]);
  return text ? <div className="toast" role="status"><CircleCheck size={18} color="#6EE7B7" />{text}</div> : null;
}

export function useToast() {
  const [text, setText] = useState<string | null>(null);
  const clear = useCallback(() => setText(null), []);
  return { text, show: setText, clear };
}

export const won = (n: number | null | undefined) => (n == null ? "Price unknown" : n === 0 ? "Free" : `₩${Math.round(n / 1000)}k`);
/** A 0–1 recommender score shown as a percentage ("fit"). */
export const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);
export const fmtDate = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
export const weekday = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-US", { weekday: "short" });
