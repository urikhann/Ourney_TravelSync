// My profile (tap your name on the home screen), issue #21:
// my saved travel preferences (the 10-question survey, used for every new trip), my travel tastes,
// language, and log out / delete account at the bottom.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, message } from "../api";
import type { Category, SavedPreferences, User } from "../types";
import { ErrorBox, fmtDate, Loading } from "../ui";
import TopBar from "../components/TopBar";
import RatingsList from "../components/RatingsList";
import TastesCard from "../components/TastesCard";
import AccountActions from "../components/AccountSheet";
import { Avatar } from "../components/Avatar";
import { LangSwitch, t } from "../i18n";

export default function Profile() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/me") });
  const prefs = useQuery({ queryKey: ["my-prefs"], queryFn: () => api.get<SavedPreferences>("/me/preferences") });
  const [ratings, setRatings] = useState<Record<Category, number> | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => { if (prefs.data && !ratings) setRatings(prefs.data.ratings); }, [prefs.data, ratings]);

  const save = useMutation({
    mutationFn: () => api.put<SavedPreferences>("/me/preferences", { ratings, expected_version: prefs.data?.version ?? 0 }),
    onSuccess: (r) => { qc.setQueryData(["my-prefs"], r); setDone(true); },
  });
  const changed = !!ratings && !!prefs.data && JSON.stringify(ratings) !== JSON.stringify(prefs.data.ratings);
  const p = prefs.data;

  return (
    <>
      <TopBar title={t("My profile")} back="/trips" />
      <main className="content">
        <div className="card pad row" style={{ gap: 12 }}>
          <Avatar name={me.data?.name ?? "?"} i={0} size={48} />
          <div className="grow" style={{ minWidth: 0 }}>
            <div className="sheet-title" style={{ margin: 0 }}>{me.data?.name}</div>
            <div className="muted small" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{me.data?.email}</div>
          </div>
          <LangSwitch compact />
        </div>

        <div className="h2">{t("My travel preferences")} <small>{p?.saved ? t("used for every new trip") : t("not saved yet")}</small></div>
        {!ratings || !p ? <Loading /> : (
          <>
            {!p.saved && <div className="banner blue" style={{ marginBottom: 10 }}>{t("Answer once and every trip you create or join is filled in for you.")}</div>}
            <RatingsList ratings={ratings} onChange={(c, v) => { setRatings({ ...ratings, [c]: v }); setDone(false); }} />
            <p className="muted tiny" style={{ margin: "8px 4px 0" }}>{t("Trips you're already in keep their own answers. Open a trip's Me tab to switch it to these.")}</p>
            <ErrorBox error={save.error ? message(save.error) : null} />
            <button className="btn" style={{ marginTop: 10 }} disabled={save.isPending || (p.saved && !changed)} onClick={() => save.mutate()}>
              {save.isPending ? t("Saving…") : done && !changed ? t("Saved") : t("Save my preferences")}
            </button>
            {p.saved && <p className="muted tiny center" style={{ marginTop: 6 }}>
              {t("Version {n} · saved {date}", { n: p.version, date: fmtDate(p.history[0].saved_at.slice(0, 10)) })}</p>}
          </>
        )}

        <TastesCard compact />

        <div className="h2">{t("Account")}</div>
        <AccountActions />
        <div style={{ height: 30 }} />
      </main>
    </>
  );
}
