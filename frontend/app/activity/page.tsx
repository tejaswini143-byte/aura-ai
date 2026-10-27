"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, setCsrfToken } from "../api";
import { useAura } from "../components/AuraProvider";

type ProviderStatus = { twilio: { configured: boolean; manual_calls_enabled: boolean; reason: string }; gemini_live: { credential_present: boolean; access_verified: boolean; voice_bridge_enabled: boolean; model?: string; reason: string } };
type AuthSession = { authenticated: boolean; configured: boolean; csrf_token: string | null; message?: string };

export default function Activity() {
  const { calls, loading, run, notice } = useAura();
  const [phone, setPhone] = useState("");
  const [purpose, setPurpose] = useState("Daily planning check-in");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<ProviderStatus | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [adminConfigured, setAdminConfigured] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [adminPassword, setAdminPassword] = useState("");

  useEffect(() => {
    api<ProviderStatus>("/api/providers/status").then(setProviders).catch(e => setError(e instanceof Error ? e.message : "Provider status is unavailable"));
    api<AuthSession>("/api/auth/session").then(session => {
      setAdminConfigured(session.configured);
      setAuthenticated(session.authenticated);
      setCsrfToken(session.csrf_token);
    }).catch(e => setError(e instanceof Error ? e.message : "Could not check admin session"))
      .finally(() => setAuthLoading(false));
  }, []);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const password = adminPassword;
      setAdminPassword("");
      const session = await api<AuthSession>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      setCsrfToken(session.csrf_token);
      setAuthenticated(session.authenticated);
      setAdminConfigured(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed.");
    }
  }

  async function call(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!authenticated) {
      setError("Sign in to AURA Settings before requesting a call.");
      return;
    }
    if (!providers?.twilio.manual_calls_enabled) {
      setError("Calling is disabled until backend configuration, explicit consent, and a verified destination are ready.");
      return;
    }
    const confirmed = window.confirm("This will request one phone call now. The call will use your configured provider and may incur charges. Continue?");
    if (!confirmed) return;
    setBusy(true);
    try {
      const result = await run("/api/calls", "POST", { phone, purpose, consent, idempotency_key: crypto.randomUUID() });
      if (result.status === "initiated" || result.status === "queued") notice("Provider accepted the request. This does not confirm that the call was answered or that AI audio worked.");
      setConsent(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Call request failed");
    } finally { setBusy(false); }
  }

  const callsEnabled = Boolean(authenticated && providers?.twilio.manual_calls_enabled);
  return <>
    <section className="panel call-panel">
      <div className="section-head"><div><span className="eyebrow">CONSENT-BASED CHECK-IN</span><h2>Request a call</h2></div><span className="status-tag">{providers?.gemini_live.voice_bridge_enabled ? "Gemini Live verified" : "Live voice awaiting connection test"}</span></div>
      <p className="muted">AURA is configured to use a bidirectional Gemini Live voice stream. Real-time speech is confirmed only after a call successfully opens the signed Twilio WebSocket and Gemini Live session.</p>
      {!providers ? <p className="muted" role="status">Checking call readiness…</p> : <div className="notice-banner"><b>{callsEnabled ? "Manual call configuration is present" : "Calls are currently disabled"}</b><span>{providers.twilio.reason}</span><span>{providers.gemini_live.reason}</span></div>}
      {authLoading ? <p className="muted" role="status">Checking secure admin session…</p>
        : adminConfigured === false ? <p className="fine-print">Admin access is not configured. Set AURA_ADMIN_PASSWORD (at least 16 characters) on the backend.</p>
        : !authenticated ? <form className="form-grid" onSubmit={signIn}>
          <label className="wide">AURA admin password<input type="password" value={adminPassword} onChange={e => setAdminPassword(e.target.value)} autoComplete="current-password" required /></label>
          <div className="wide"><button disabled={!adminPassword}>Sign in to request a call</button></div>
        </form> : <form className="form-grid" onSubmit={call}>
        <label>Verified phone number (E.164)<input value={phone} onChange={e => setPhone(e.target.value)} placeholder="+919876543210" required autoComplete="tel" /></label>
        <label>Purpose<input value={purpose} onChange={e => setPurpose(e.target.value)} required /></label>
        <label className="wide checkbox"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /><span>I confirm that the recipient explicitly agreed to receive this call.</span></label>
        <div className="wide"><button disabled={busy || !consent || !callsEnabled}>{busy ? "Submitting…" : "Confirm and request call"}</button></div>
      </form>}
      <p className="fine-print">Each manual call requires your explicit confirmation, calling consent in Settings, and the same verified phone number saved there. Calls may incur charges. A configured model is not proof of a successful live conversation.</p>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
    <section className="panel">
      <div className="section-head"><div><span className="eyebrow">RECENT ACTIVITY</span><h2>Call history</h2></div><span className="status-tag">{calls.length} records</span></div>
      {loading ? <div className="loading-state">Loading call history…</div> : calls.length === 0 ? <div className="empty-state"><span className="empty-state-icon">◷</span><p>No calls yet. Your call history will appear here.</p></div> : <div className="task-list">{calls.map(c => <article className="planner-task" key={c.id}><span className="metric-icon violet">◷</span><div className="planner-task-content"><b>{c.purpose}</b><small>{c.phone} · {new Date(c.created_at).toLocaleString()}</small>{c.transcript && <p>Response captured: {c.transcript}</p>}</div><span className="status-tag">{c.status}</span><span className={c.consent ? "consent-ok" : "consent-missing"}>{c.consent ? "Consent recorded" : "Consent absent"}</span></article>)}</div>}
      <p className="fine-print">Calls are not emergency monitoring. API acceptance is not proof of an answered call or working two-way AI conversation.</p>
    </section>
  </>;
}
