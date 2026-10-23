// Sign up in two steps: (1) name, email, password + confirm password, (2) the 6-digit code from the email.
import { FormEvent, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Mail, User, X } from "lucide-react";
import { api, message, setToken } from "../api";
import type { TokenOut } from "../types";
import { ErrorBox } from "../ui";
import AuthShell from "../components/AuthShell";
import PasswordInput from "../components/PasswordInput";

// How strong is the password? 0 = too short, 1 = okay, 2 = good, 3 = strong. Only "8+ characters" is required.
function strength(pw: string): 0 | 1 | 2 | 3 {
  if (pw.length < 8) return 0;
  let s = 1;
  if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) s++;
  if (pw.length >= 12 || /[^A-Za-z0-9]/.test(pw)) s++;
  return s as 1 | 2 | 3;
}
const STRENGTH = ["Too short", "Okay", "Good", "Strong"];

export default function Signup() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const next = params.get("next") || "/trips";
  const verifyEmail = params.get("verify");
  const [step, setStep] = useState<"form" | "code">(verifyEmail ? "code" : "form");
  const [name, setName] = useState("");
  const [email, setEmail] = useState(verifyEmail ?? "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);

  const level = strength(password);
  const matches = confirm.length > 0 && confirm === password;
  const canSubmit = name.trim() !== "" && email !== "" && level > 0 && matches && !busy;

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null); setInfo(null);
    try { await fn(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }

  const signup = (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) { setError("The two passwords are different."); return; }
    run(async () => {
      await api.post("/auth/signup", { email, name: name.trim(), password });  // the confirm field never leaves the browser
      setStep("code");
    });
  };

  const verify = (e: FormEvent) => { e.preventDefault(); run(async () => {
    const r = await api.post<TokenOut>("/auth/verify-email", { email, code });
    setToken(r.access_token);
    qc.setQueryData(["me"], r.user);
    nav(next, { replace: true });
  }); };

  const resend = () => run(async () => {
    await api.post("/auth/resend-code", { email });
    setInfo("A new code is on its way (unless you asked for one less than a minute ago).");
  });

  // ---------- step 2: the code ----------
  if (step === "code")
    return (
      <AuthShell title="Check your email" sub={<>We sent a 6-digit code to <b>{email}</b>.</>}>
        <form className="auth-form" onSubmit={verify}>
          <label className="auth-field">
            <span>Verification code</span>
            {/* One real input (for typing, pasting and autofill) drawn as six boxes. */}
            <div className="code-boxes" onClick={() => codeRef.current?.focus()}>
              <input ref={codeRef} id="verify-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus
                aria-label="6-digit code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
              {Array.from({ length: 6 }, (_, i) => (
                <span key={i} className={"code-box" + (i === Math.min(code.length, 5) ? " on" : "")} aria-hidden="true">{code[i] ?? ""}</span>
              ))}
            </div>
          </label>
          <p className="auth-hint">While developing, the code is printed in the backend terminal.</p>
          <ErrorBox error={error} />
          {info && <div className="auth-note">{info}</div>}
          <button className="btn blue" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Verify and continue"}</button>
          <div className="auth-row-links">
            <button type="button" className="auth-link" onClick={resend} disabled={busy}>Send a new code</button>
            <button type="button" className="auth-link" onClick={() => { setStep("form"); setCode(""); }}>Use a different email</button>
          </div>
        </form>
      </AuthShell>
    );

  // ---------- step 1: the form ----------
  return (
    <AuthShell title="Create your account" sub="Plan one trip that everyone is happy with.">
      <form className="auth-form" onSubmit={signup}>
        <label className="auth-field">
          <span>Name</span>
          <div className="inp">
            <User size={17} className="inp-ico" aria-hidden="true" />
            <input id="signup-name" autoComplete="name" placeholder="What your friends call you" required maxLength={80}
              value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        </label>
        <label className="auth-field">
          <span>Email</span>
          <div className="inp">
            <Mail size={17} className="inp-ico" aria-hidden="true" />
            <input id="signup-email" type="email" autoComplete="email" placeholder="you@example.com" required
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </label>
        <label className="auth-field">
          <span>Password</span>
          <PasswordInput id="signup-password" autoComplete="new-password" placeholder="At least 8 characters" required minLength={8}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {password && (
            <div className="pw-meter" data-level={level}>
              <div className="pw-bars"><i /><i /><i /></div>
              <span>{STRENGTH[level]}</span>
            </div>
          )}
        </label>
        <label className="auth-field">
          <span>Confirm password</span>
          <PasswordInput id="signup-confirm" autoComplete="new-password" placeholder="Type it again" required
            value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={confirm.length > 0 && !matches} />
          {confirm && (
            <div className={"pw-match " + (matches ? "ok" : "bad")} role="status">
              {matches ? <><Check size={14} /> Passwords match</> : <><X size={14} /> Passwords don't match yet</>}
            </div>
          )}
        </label>
        <ErrorBox error={error} />
        <button className="btn blue" disabled={!canSubmit}>{busy ? "Creating account…" : "Create account"}</button>
      </form>
      <p className="auth-foot">
        Already have an account? <Link to={`/login?next=${encodeURIComponent(next)}`}>Log in</Link>
      </p>
    </AuthShell>
  );
}
