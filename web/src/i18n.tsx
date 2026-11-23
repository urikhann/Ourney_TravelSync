// English / Korean.
// Screens write the English text and wrap it: t("Join this trip") or t("{n} friends", { n: 3 }).
// In Korean the same English text is looked up in i18n.ko.ts; anything not translated yet stays English.
// The choice is saved in the browser and the whole screen re-renders when it changes (see LangProvider).
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { Globe } from "lucide-react";
import { KO } from "./i18n.ko";

export type Lang = "en" | "ko";
const KEY = "ts_lang";

function first(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "en" || saved === "ko") return saved;
  } catch { /* private mode */ }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ko") ? "ko" : "en";
}

let current: Lang = first();

/** The language in use right now (for code outside components, e.g. date formatting). */
export const lang = () => current;
export const locale = () => (current === "ko" ? "ko-KR" : "en-US");

/** Translate one English UI text. {name} placeholders are filled from vars. */
export function t(text: string, vars?: Record<string, string | number>): string {
  let out = current === "ko" ? KO[text] ?? text : text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m: string, k: string) => (k in vars ? String(vars[k]) : m));
  return out;
}

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({ lang: current, setLang: () => {} });

/** Wrap the app once. Changing the language re-draws every screen (keyed), so every t() call updates. */
export function LangProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState<Lang>(current);
  const setLang = useCallback((l: Lang) => {
    current = l;
    try { localStorage.setItem(KEY, l); } catch { /* private mode */ }
    setValue(l);
  }, []);
  useEffect(() => { document.documentElement.lang = value; }, [value]);
  return <Ctx.Provider value={{ lang: value, setLang }}><div key={value} style={{ display: "contents" }}>{children}</div></Ctx.Provider>;
}

export const useLang = () => useContext(Ctx);

/** The EN / 한국어 switch. `compact`: one small button that switches to the other language (for headers). */
export function LangSwitch({ dark = false, compact = false }: { dark?: boolean; compact?: boolean }) {
  const { lang: l, setLang } = useLang();
  if (compact)
    return (
      <button type="button" className="lang-toggle" onClick={() => setLang(l === "ko" ? "en" : "ko")}
        aria-label={l === "ko" ? "Switch to English" : "한국어로 바꾸기"}>
        <Globe size={15} aria-hidden="true" /> {l === "ko" ? "EN" : "한국어"}
      </button>
    );
  return (
    <div className={"lang-switch" + (dark ? " dark" : "")} role="group" aria-label="Language / 언어">
      <button type="button" aria-pressed={l === "en"} onClick={() => setLang("en")}>EN</button>
      <button type="button" aria-pressed={l === "ko"} onClick={() => setLang("ko")}>한국어</button>
    </div>
  );
}
