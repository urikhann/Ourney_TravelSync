// Log in screen. Layout + routes artwork come from components/AuthShell.tsx.
import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Mail, Ticket } from "lucide-react";
import { api, ApiError, message, setToken } from "../api";
import type { TokenOut } from "../types";
import { ErrorBox } from "../ui";
import AuthShell from "../components/AuthShell";
import PasswordInput from "../components/PasswordInput";

export default function Login() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const next = params.get("next") || "/trips";
  const invited = next.startsWith("/join/");          // came here from an invite link
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.post<TokenOut>("/auth/login", { email, password });
      setToken(r.access_token);
      qc.setQueryData(["me"], r.user);
      nav(next, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "email_not_verified") {
        nav(`/signup?verify=${encodeURIComponent(email)}&next=${encodeURIComponent(next)}`);
        return;
      }
      setError(message(err));
    } finally { setBusy(false); }
  }

  return (
    <AuthShell title="Welcome back" sub="Different tastes, one itinerary.">
      {invited && (
        <div className="auth-note"><Ticket size={16} /> Log in to see the trip you were invited to.</div>
      )}
      <form className="auth-form" onSubmit={submit}>
        <label className="auth-field">
          <span>Email</span>
          <div className="inp">
            <Mail size={17} className="inp-ico" aria-hidden="true" />
            <input id="login-email" type="email" autoComplete="email" placeholder="you@example.com" required
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </label>
        <label className="auth-field">
          <span className="auth-label-row">Password <Link to="/forgot" className="auth-link">Forgot password?</Link></span>
          <PasswordInput id="login-password" autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <ErrorBox error={error} />
        <button className="btn blue" disabled={busy}>{busy ? "Logging in…" : "Log in"}</button>
      </form>
      <p className="auth-foot">
        New to TravelSync? <Link to={`/signup?next=${encodeURIComponent(next)}`}>Create an account</Link>
      </p>
    </AuthShell>
  );
}
