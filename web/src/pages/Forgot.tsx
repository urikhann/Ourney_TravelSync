import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, message, setToken } from "../api";
import type { TokenOut } from "../types";
import { ErrorBox } from "../ui";
import TopBar from "../components/TopBar";

export default function Forgot() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState<"email" | "reset">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  const send = (e: FormEvent) => { e.preventDefault(); run(async () => { await api.post("/auth/forgot-password", { email }); setStep("reset"); }); };
  const reset = (e: FormEvent) => { e.preventDefault(); run(async () => {
    const r = await api.post<TokenOut>("/auth/reset-password", { email, code, new_password: password });
    setToken(r.access_token); qc.setQueryData(["me"], r.user); nav("/trips", { replace: true });
  }); };

  return (
    <>
      <TopBar title="Reset password" back="/login" />
      <main className="content">
        {step === "email" ? (
          <form className="stack" onSubmit={send}>
            <p className="muted small">Enter your email and we'll send you a 6-digit code.</p>
            <label className="field">Email<input id="forgot-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <ErrorBox error={error} />
            <button className="btn blue" disabled={busy}>Send code</button>
          </form>
        ) : (
          <form className="stack" onSubmit={reset}>
            <p className="muted small">If <b>{email}</b> has an account, a code is on its way.</p>
            <label className="field">Code<input id="reset-code" className="code-input" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} /></label>
            <label className="field">New password (8+ characters)<input id="reset-password" type="password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <ErrorBox error={error} />
            <button className="btn blue" disabled={busy || code.length !== 6 || password.length < 8}>Set new password</button>
          </form>
        )}
      </main>
    </>
  );
}
