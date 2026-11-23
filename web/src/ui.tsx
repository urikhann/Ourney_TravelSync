import { useCallback, useEffect, useState } from "react";
import { CircleCheck } from "lucide-react";
import { locale, t } from "./i18n";

export function ErrorBox({ error }: { error: string | null | undefined }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export function Loading() {
  return <div className="empty muted">{t("Loading…")}</div>;
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

export const won = (n: number | null | undefined) => (n == null ? t("Price unknown") : n === 0 ? t("Free")
  : locale() === "ko-KR" ? `${(Math.round(n / 1000) * 1000).toLocaleString("ko-KR")}원` : `₩${Math.round(n / 1000)}k`);
/** A 0–1 recommender score shown as a percentage ("fit"). */
export const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);
export const fmtDate = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString(locale(), { weekday: "short", month: "short", day: "numeric" });
export const weekday = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(locale(), { weekday: "short" });

/** Recommendation reasons come from the server in English ("2 of 3 love cafés"); show them in the UI language. */
export function reasonText(text: string): string {
  let m = text.match(/^(\d+) of (\d+) (.+)$/);
  if (m) return t("{a} of {n} {what}", { a: m[1], n: m[2], what: t(m[3]) });
  m = text.match(/^Matches (.+)$/);
  if (m) return t("Matches {what}", { what: t(m[1]) });
  m = text.match(/^Fair pick for (.+)$/);
  if (m) return t("Fair pick for {name}", { name: m[1] === "a member" ? t("a member") : m[1] });
  return t(text);
}
