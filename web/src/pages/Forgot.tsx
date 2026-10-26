// web/src/pages/Forgot.tsx  (replace the whole file)
// Reset password in two steps: (1) email -> a 6-digit code is sent,
// (2) code + new password + confirm password. The confirm field never leaves the browser.
import { FormEvent, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Mail, X } from "lucide-react";
import { api, message, setToken } from "../api";
import type { TokenOut } from "../types";
import { ErrorBox } from "../ui";
import AuthShell from "../components/AuthShell";
import PasswordInput from "../components/PasswordInput";
import { STRENGTH, strength } from "../password";

export default function Forgot() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState<"email" | "reset">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);

  const level = strength(password);
  const matches = confirm.length > 0 && confirm === password;
  const canReset = code.length === 6 && level > 0 && matches && !busy;

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null); setInfo(null);
    try { await fn(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }

  const send = (e: FormEvent) => {
    e.preventDefault();
    run(async () => { await api.post("/auth/forgot-password", { email }); setStep("reset"); });
  };

  const resend = () => run(async () => {
    await api.post("/auth/forgot-password", { email });
    setInfo("A new code is on its way (unless you asked for one less than a minute ago).");
  });

  const reset = (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) { setError("The two passwords are different."); return; }
    run(async () => {
      const r = await api.post<TokenOut>("/auth/reset-password", { email, code, new_password: password });
      setToken(r.access_token);
      qc.setQueryData(["me"], r.user);
      nav("/trips", { replace: true });
    });
  };

  // ---------- step 1: email ----------
  if (step === "email")
    return (
      <AuthShell title="Reset your password" sub="Enter your email and we'll send you a 6-digit code.">
        <form className="auth-form" onSubmit={send}>
          <label className="auth-field">
            <span>Email</span>
            <div className="inp">
              <Mail size={17} className="inp-ico" aria-hidden="true" />
              <input id="forgot-email" type="email" autoComplete="email" placeholder="you@example.com" required autoFocus
                value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
          </label>
          <ErrorBox error={error} />
          <button className="btn blue" disabled={busy || email === ""}>{busy ? "Sending…" : "Send code"}</button>
        </form>
        <p className="auth-foot">Remembered it? <Link to="/login">Back to log in</Link></p>
      </AuthShell>
    );

  // ---------- step 2: code + new password + confirm ----------
  return (
    <AuthShell title="Choose a new password" sub={<>If <b>{email}</b> has an account, a code is on its way.</>}>
      <form className="auth-form" onSubmit={reset}>
        <label className="auth-field">
          <span>Code from the email</span>
          {/* One real input (typing, pasting, autofill) drawn as six boxes, same as sign-up. */}
          <div className="code-boxes" onClick={() => codeRef.current?.focus()}>
            <input ref={codeRef} id="reset-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus
              aria-label="6-digit code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            {Array.from({ length: 6 }, (_, i) => (
              <span key={i} className={"code-box" + (i === Math.min(code.length, 5) ? " on" : "")} aria-hidden="true">{code[i] ?? ""}</span>
            ))}
          </div>
        </label>

        <label className="auth-field">
          <span>New password</span>
          <PasswordInput id="reset-password" autoComplete="new-password" placeholder="At least 8 characters" required minLength={8}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {password && (
            <div className="pw-meter" data-level={level}>
              <div className="pw-bars"><i /><i /><i /></div>
              <span>{STRENGTH[level]}</span>
            </div>
          )}
        </label>

        <label className="auth-field">
          <span>Confirm new password</span>
          <PasswordInput id="reset-confirm" autoComplete="new-password" placeholder="Type it again" required
            value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={confirm.length > 0 && !matches} />
          {confirm && (
            <div className={"pw-match " + (matches ? "ok" : "bad")} role="status">
              {matches ? <><Check size={14} /> Passwords match</> : <><X size={14} /> Passwords don't match yet</>}
            </div>
          )}
        </label>

        <ErrorBox error={error} />
        {info && <div className="auth-note">{info}</div>}
        <button className="btn blue" disabled={!canReset}>{busy ? "Saving…" : "Set new password"}</button>
        <div className="auth-row-links">
          <button type="button" className="auth-link" onClick={resend} disabled={busy}>Send a new code</button>
          <button type="button" className="auth-link" onClick={() => { setStep("email"); setCode(""); }}>Use a different email</button>
        </div>
      </form>
    </AuthShell>
  );
}
