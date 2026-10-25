"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { useAura } from "../components/AuraProvider";

type ProviderStatus = { twilio: { configured: boolean; manual_calls_enabled: boolean; reason: string }; gemini_live: { voice_bridge_enabled: boolean; reason: string } };

export default function Activity() {
  const { calls, loading, run, notice } = useAura();
  const [phone, setPhone] = useState("");
  const [purpose, setPurpose] = useState("Daily planning check-in");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<ProviderStatus | null>(null);

  useEffect(() => {
    api<ProviderStatus>("/api/providers/status").then(setProviders).catch(e => setError(e instanceof Error ? e.message : "Provider status is unavailable"));
  }, []);

  async function call(event: FormEvent) {
    event.preventDefault();
    setError("");
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

  const callsEnabled = Boolean(providers?.twilio.manual_calls_enabled);
  return <>
    <section className="panel call-panel">
      <div className="section-head"><div><span className="eyebrow">CONSENT-BASED CHECK-IN</span><h2>Request a call</h2></div><span className="status-tag">Twilio prototype · no live AI audio</span></div>
      <p className="muted">Calls use the existing Twilio speech-capture prototype. AURA does not have an interactive Gemini voice bridge yet.</p>
      {!providers ? <p className="muted" role="status">Checking call readiness…</p> : <div className="notice-banner"><b>{callsEnabled ? "Manual call configuration is present" : "Calls are currently disabled"}</b><span>{providers.twilio.reason}</span><span>{providers.gemini_live.reason}</span></div>}
      <form className="form-grid" onSubmit={call}>
        <label>Verified phone number (E.164)<input value={phone} onChange={e => setPhone(e.target.value)} placeholder="+919876543210" required autoComplete="tel" /></label>
        <label>Purpose<input value={purpose} onChange={e => setPurpose(e.target.value)} required /></label>
        <label className="wide checkbox"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /><span>I confirm that the recipient explicitly agreed to receive this call.</span></label>
        <div className="wide"><button disabled={busy || !consent || !callsEnabled}>{busy ? "Submitting…" : "Confirm and request call"}</button></div>
      </form>
      <p className="fine-print">An explicit browser confirmation is required. The backend also requires its call-enable switch and a verified saved number. This project currently has no phone-verification flow, so calls remain blocked.</p>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
    <section className="panel">
      <div className="section-head"><div><span className="eyebrow">RECENT ACTIVITY</span><h2>Call history</h2></div><span className="status-tag">{calls.length} records</span></div>
      {loading ? <div className="loading-state">Loading call history…</div> : calls.length === 0 ? <div className="empty-state"><span className="empty-state-icon">◷</span><p>No calls yet. Your call history will appear here.</p></div> : <div className="task-list">{calls.map(c => <article className="planner-task" key={c.id}><span className="metric-icon violet">◷</span><div className="planner-task-content"><b>{c.purpose}</b><small>{c.phone} · {new Date(c.created_at).toLocaleString()}</small>{c.transcript && <p>Response captured: {c.transcript}</p>}</div><span className="status-tag">{c.status}</span><span className={c.consent ? "consent-ok" : "consent-missing"}>{c.consent ? "Consent recorded" : "Consent absent"}</span></article>)}</div>}
      <p className="fine-print">Calls are not emergency monitoring. API acceptance is not proof of an answered call or working two-way AI conversation.</p>
    </section>
  </>;
}
