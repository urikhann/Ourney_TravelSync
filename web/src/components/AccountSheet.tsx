// Bottom of the profile page: log out, and delete account (issue #18).
// Deleting needs the password again. Trips you own pass to the next member, or are deleted if you're alone.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut, Trash2 } from "lucide-react";
import { api, message, setToken } from "../api";
import { t } from "../i18n";
import { ErrorBox } from "../ui";
import PasswordInput from "./PasswordInput";

export default function AccountActions() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const [password, setPassword] = useState("");
  const logout = () => { setToken(null); qc.clear(); nav("/login"); };
  const del = useMutation({ mutationFn: () => api.post("/me/delete", { password }), onSuccess: logout });

  return (
    <div className="stack" style={{ gap: 8 }}>
      <button className="btn ghost sm" onClick={logout}><LogOut size={15} /> {t("Log out")}</button>
      {deleting ? (
        <form className="confirm-box" onSubmit={(e) => { e.preventDefault(); del.mutate(); }}>
          <div className="b">{t("Delete your account?")}</div>
          <p className="muted small">{t("You leave every trip. Trips you own pass to the next member, or are deleted if nobody else is in them. This can't be undone.")}</p>
          <label className="auth-field"><span>{t("Password")}</span>
            <PasswordInput id="delete-password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <ErrorBox error={del.error ? message(del.error) : null} />
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <button type="button" className="btn ghost sm grow" onClick={() => { setDeleting(false); setPassword(""); }}>{t("Cancel")}</button>
            <button className="btn sm grow danger" disabled={!password || del.isPending}>{t("Delete account")}</button>
          </div>
        </form>
      ) : (
        <button className="btn ghost sm danger-text" onClick={() => setDeleting(true)}><Trash2 size={15} /> {t("Delete account")}</button>
      )}
    </div>
  );
}
