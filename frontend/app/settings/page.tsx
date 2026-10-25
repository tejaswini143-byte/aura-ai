"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, setCsrfToken } from "../api";

type CompanionPrefs = {
  preferred_language: string; mixed_language: boolean; personality: string;
  response_length: string; communication_tone: string;
  voice_preference: string; calling_consent: boolean; automatic_mood_calls: boolean;
  quiet_hours_start: string; quiet_hours_end: string; timezone: string;
  destination_phone_present: boolean; destination_phone_masked: string | null;
  phone_verified: boolean; reminder_calls_enabled: boolean; phone_storage_consent: boolean; store_call_transcripts: boolean;
  calling_consent_at?: string | null;
};
type ProviderStatus = {
  gemini: { configured: boolean };
  twilio: { configured: boolean; manual_calls_enabled: boolean; automatic_calls_enabled: boolean; destination_verification_available: boolean };
  dotvoice: { configured: boolean; verified: boolean; calls_enabled: boolean };
  gnani: { configured: boolean; voice_bridge_enabled: boolean };
  gemini_live: { credential_present: boolean; model?: string; access_verified: boolean; bridge_implemented?: boolean; voice_bridge_enabled: boolean; reason?: string };
};
type AuthSession = { authenticated: boolean; configured: boolean; csrf_token: string | null; message?: string; expires_in_seconds?: number };
type VerificationResult = { status: string; message: string };
type VerificationStatus = { status: string; retry_after_seconds: number; attempts_remaining: number; expires_at: string | null };
const initial: CompanionPrefs = {
  preferred_language: "English", mixed_language: false, personality: "Friend",
  response_length: "Balanced", communication_tone: "Gentle", voice_preference: "Default", calling_consent: false, automatic_mood_calls: false,
  quiet_hours_start: "22:00", quiet_hours_end: "08:00", timezone: "Asia/Kolkata",
  destination_phone_present: false, destination_phone_masked: null, phone_verified: false,
  reminder_calls_enabled: false, phone_storage_consent: false, store_call_transcripts: false,
};
const checkLabel = (enabled: boolean) => enabled ? "Configured" : "Not configured";

export default function Settings() {
  const [reminders, setReminders] = useState(true);
  const [displayName, setName] = useState("");
  const [saved, setSaved] = useState(false);
  const [prefs, setPrefs] = useState(initial);
  const [phoneInput, setPhoneInput] = useState("");
  const [storePhone, setStorePhone] = useState(false);
  const [clearPhone, setClearPhone] = useState(false);
  const [providers, setProviders] = useState<ProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [authLoaded, setAuthLoaded] = useState(false);
  const [adminConfigured, setAdminConfigured] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [adminPassword, setAdminPassword] = useState("");
  const [verificationSmsConsent, setVerificationSmsConsent] = useState(false);
  const [verificationCode, setVerificationCode] = useState("");
  const [verificationPending, setVerificationPending] = useState(false);
  const [verificationState, setVerificationState] = useState("none");
  const [attemptsRemaining, setAttemptsRemaining] = useState(5);
  const [verificationBusy, setVerificationBusy] = useState(false);
  const [verificationNotice, setVerificationNotice] = useState("");
  const [resendSeconds, setResendSeconds] = useState(0);

  async function loadBackendSettings() {
    const [settings, providerStatus, verification] = await Promise.all([
      api<CompanionPrefs>("/api/companion/preferences"),
      api<ProviderStatus>("/api/providers/status"),
      api<VerificationStatus>("/api/phone-verification/status"),
    ]);
    setPrefs(settings);
    setProviders(providerStatus);
    setVerificationPending(verification.status === "pending");
    setVerificationState(verification.status);
    setAttemptsRemaining(verification.attempts_remaining);
    setResendSeconds(verification.retry_after_seconds);
  }

  useEffect(() => {
    setReminders(localStorage.getItem("aura-reminders") !== "false");
    setName(localStorage.getItem("aura-display-name") || "");
    api<AuthSession>("/api/auth/session").then(async session => {
      setAdminConfigured(session.configured);
      setAuthenticated(session.authenticated);
      setCsrfToken(session.csrf_token);
      if (session.authenticated) await loadBackendSettings();
    }).catch(e => { setAdminConfigured(null); setError(e instanceof Error ? e.message : "Could not check admin session"); })
      .finally(() => { setAuthLoaded(true); setLoading(false); });
  }, []);

  useEffect(() => {
    if (resendSeconds <= 0) return;
    const timer = window.setTimeout(() => setResendSeconds(value => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [resendSeconds]);

  async function signIn(e: FormEvent) {
    e.preventDefault(); setError(""); setNotice(""); setLoading(true);
    try {
      const password = adminPassword;
      setAdminPassword("");
      const session = await api<AuthSession>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      setCsrfToken(session.csrf_token); setAuthenticated(true); setAdminConfigured(true);
      await loadBackendSettings();
    } catch (e) { setError(e instanceof Error ? e.message : "Sign in failed."); }
    finally { setLoading(false); }
  }

  async function signOut() {
    setError("");
    try { await api("/api/auth/logout", { method: "POST" }); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not sign out."); }
    setCsrfToken(null); setAuthenticated(false); setProviders(null); setVerificationCode(""); setVerificationPending(false); setVerificationState("none");
  }

  async function startVerification() {
    setVerificationBusy(true); setVerificationNotice(""); setError("");
    setVerificationSmsConsent(false);
    try {
      const result = await api<VerificationResult>("/api/phone-verification/start", { method: "POST", body: JSON.stringify({ sms_consent: verificationSmsConsent }) });
      setVerificationPending(result.status === "pending"); setVerificationState(result.status); setAttemptsRemaining(5); setVerificationCode(""); setResendSeconds(60); setVerificationNotice(result.message);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not request a verification code.";
      if (message.toLowerCase().includes("recently")) setResendSeconds(60);
      setError(message);
      try {
        const status = await api<VerificationStatus>("/api/phone-verification/status");
        setVerificationState(status.status); setVerificationPending(status.status === "pending"); setAttemptsRemaining(status.attempts_remaining); setResendSeconds(status.retry_after_seconds);
      } catch { /* keep the original request error visible */ }
    }
    finally { setVerificationBusy(false); }
  }

  async function checkVerification() {
    setVerificationBusy(true); setVerificationNotice(""); setError("");
    try {
      const result = await api<VerificationResult>("/api/phone-verification/check", { method: "POST", body: JSON.stringify({ code: verificationCode.trim() }) });
      if (result.status === "verified") {
        setPrefs(current => ({ ...current, phone_verified: true })); setVerificationPending(false); setVerificationState("verified"); setAttemptsRemaining(0); setVerificationCode("");
      }
      setVerificationNotice(result.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not verify the code.");
      try {
        const status = await api<VerificationStatus>("/api/phone-verification/status");
        setVerificationState(status.status); setVerificationPending(status.status === "pending"); setAttemptsRemaining(status.attempts_remaining);
      } catch { /* keep the verification error visible if the status refresh fails */ }
    }
    finally { setVerificationBusy(false); }
  }

  if (!authLoaded) return <section className="panel settings-panel"><p role="status">Checking secure admin session…</p></section>;
  if (adminConfigured === null) return <section className="panel settings-panel"><h2>Backend session unavailable</h2><p className="muted">AURA could not check the backend session. Confirm the backend is running, then reload this page.</p>{error && <p className="inline-error" role="alert">{error}</p>}</section>;
  if (adminConfigured === false) return <section className="panel settings-panel">
    <span className="eyebrow">ADMIN SIGN-IN</span><h2>Secure Settings are disabled</h2>
    <p className="muted">Set a strong server-only AURA_ADMIN_PASSWORD (at least 16 characters) in backend/.env, then restart the backend. No default password is used.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
  if (!authenticated) return <section className="panel settings-panel">
    <span className="eyebrow">ADMIN SIGN-IN</span><h2>Sign in to AURA Settings</h2>
    <p className="muted">Your password is sent only to the backend for verification. AURA stores an opaque HttpOnly session cookie; the password is not saved in browser storage.</p>
    <form onSubmit={signIn}>
      <label>Admin password<input type="password" value={adminPassword} onChange={e => setAdminPassword(e.target.value)} autoComplete="current-password" required /></label>
      <button disabled={!adminPassword}>Sign in</button>
    </form>
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;

  function saveLocal() {
    localStorage.setItem("aura-reminders", String(reminders));
    localStorage.setItem("aura-display-name", displayName);
    setSaved(true);
  }

  async function savePrefs(e: FormEvent) {
    e.preventDefault();
    setSaving(true); setError(""); setNotice("");
    try {
      const next = await api<CompanionPrefs>("/api/companion/preferences", {
        method: "PUT",
        body: JSON.stringify({ ...prefs, destination_phone: phoneInput.trim() || null, store_phone_with_consent: storePhone, clear_destination_phone: clearPhone }),
      });
      setPrefs(next);
      setVerificationPending(next.destination_phone_present && !next.phone_verified);
      setVerificationState(next.phone_verified ? "verified" : "none");
      setPhoneInput(""); setStorePhone(false); setClearPhone(false);
      setVerificationSmsConsent(false); setVerificationCode(""); setVerificationNotice("");
      setNotice("Companion preferences saved to this AURA backend.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save companion preferences");
    } finally { setSaving(false); }
  }

  return <>
    <section className="panel settings-panel">
      <div className="section-head"><div><span className="eyebrow">COMPANION PREFERENCES</span></div><button type="button" className="btn-secondary" onClick={signOut}>Sign out</button></div>
      <h2>Make AURA feel like yours</h2>
      <p className="muted">These preferences are saved by the backend. Personality and voice are interaction styles; they do not mean AURA is human or a relative.</p>
      {loading ? <p role="status">Loading preferences…</p> : <form onSubmit={savePrefs}>
        <label>Preferred language<select value={prefs.preferred_language} onChange={e => setPrefs({ ...prefs, preferred_language: e.target.value })}><option>English</option><option>Hindi</option><option>Telugu</option></select></label>
        <label className="setting-toggle"><span>Mixed-language replies<small>Model support may vary; output has not been independently language-certified.</small></span><input type="checkbox" checked={prefs.mixed_language} onChange={e => setPrefs({ ...prefs, mixed_language: e.target.checked })} /></label>
        <label>Conversation style<select value={prefs.personality} onChange={e => setPrefs({ ...prefs, personality: e.target.value })}>{["Friend", "Bro", "Sis", "Mother", "Father", "Loving Companion", "Boyfriend"].map(x => <option key={x}>{x}</option>)}</select></label>
        <label>Response length<select value={prefs.response_length} onChange={e => setPrefs({ ...prefs, response_length: e.target.value })}>{["Brief", "Balanced", "Detailed"].map(x => <option key={x}>{x}</option>)}</select></label>
        <label>Communication tone<select value={prefs.communication_tone} onChange={e => setPrefs({ ...prefs, communication_tone: e.target.value })}>{["Gentle", "Casual", "Practical"].map(x => <option key={x}>{x}</option>)}</select></label>
        <label>Voice style preference<select value={prefs.voice_preference} onChange={e => setPrefs({ ...prefs, voice_preference: e.target.value })}>{["Default", "Warm", "Calm", "Bright"].map(x => <option key={x}>{x}</option>)}</select></label>
        <p className="fine-print">Voice style is a saved preference for future integrations; current calls use the existing Twilio prototype voice.</p>
        <label className="setting-toggle"><span>Consent to individually requested calls<small>A separate confirmation is still required for each call request.</small></span><input type="checkbox" checked={prefs.calling_consent} onChange={e => setPrefs({ ...prefs, calling_consent: e.target.checked, automatic_mood_calls: e.target.checked ? prefs.automatic_mood_calls : false, reminder_calls_enabled: e.target.checked ? prefs.reminder_calls_enabled : false })} /></label>
        <label className="setting-toggle"><span>Save speech transcripts from calls<small>Off by default. This controls transcript storage only; the current Twilio call is not a realtime AI conversation.</small></span><input type="checkbox" checked={prefs.store_call_transcripts} onChange={e => setPrefs({ ...prefs, store_call_transcripts: e.target.checked })} /></label>
        <label>Destination phone (E.164)<input type="tel" value={phoneInput} onChange={e => { setPhoneInput(e.target.value); setClearPhone(false); }} placeholder="+919876543210" autoComplete="tel" /></label>
        <label className="setting-toggle"><span>Allow AURA to store this number<small>Required before the backend stores a number. It will be masked in API responses.</small></span><input type="checkbox" checked={storePhone} onChange={e => setStorePhone(e.target.checked)} /></label>
        {prefs.destination_phone_present && <label className="setting-toggle"><span>Remove saved destination<small>Currently stored as {prefs.destination_phone_masked}; verification will be cleared.</small></span><input type="checkbox" checked={clearPhone} onChange={e => { setClearPhone(e.target.checked); if (e.target.checked) { setPhoneInput(""); setPrefs({ ...prefs, automatic_mood_calls: false, reminder_calls_enabled: false }); } }} /></label>}
        <div className="setting-info"><b>Destination verification</b><span>{prefs.destination_phone_present ? `${prefs.destination_phone_masked} · ${prefs.phone_verified ? "verified by Twilio Verify" : "not verified"}` : "Save a destination number with storage consent before requesting verification."}</span>
          {verificationState === "expired" && <span role="status">The code expired. Request a new code.</span>}
          {verificationState === "locked" && <span role="status">The attempt limit was reached. Request a new code after the resend cooldown.</span>}
          {verificationState === "failed" && <span role="status">The verification provider could not start the request. You may retry after the cooldown.</span>}
        </div>
        {prefs.destination_phone_present && !prefs.phone_verified && <div className="verification-controls">
          <p className="fine-print">AURA will send one SMS containing a verification code to the saved number. Your carrier may charge for SMS. This only verifies control of the number; it does not enable calls.</p>
          <label className="setting-toggle"><span>I consent to receive one verification SMS at {prefs.destination_phone_masked}<small>This consent applies to requesting this code. It is separate from consent to store the number or receive future calls.</small></span><input type="checkbox" checked={verificationSmsConsent} onChange={e => setVerificationSmsConsent(e.target.checked)} /></label>
          <button type="button" disabled={verificationBusy || !verificationSmsConsent || !providers?.twilio.destination_verification_available || resendSeconds > 0} onClick={startVerification}>
            {verificationBusy ? "Requesting…" : resendSeconds > 0 ? `Request another code in ${resendSeconds}s` : "Send verification code"}
          </button>
          {!providers?.twilio.destination_verification_available && <p className="fine-print">Verification is unavailable until the backend admin password, Twilio Verify service, and server feature flag are configured.</p>}
          {verificationPending && <div className="verification-code-form">
            <label>Verification code<input inputMode="numeric" autoComplete="one-time-code" maxLength={10} value={verificationCode} onChange={e => setVerificationCode(e.target.value.replace(/\D/g, "").slice(0, 10))} /></label>
            <small className="fine-print">{attemptsRemaining} attempts remaining. Codes expire after 10 minutes.</small>
            <button type="button" disabled={verificationBusy || verificationCode.length < 4} onClick={checkVerification}>{verificationBusy ? "Checking…" : "Verify destination"}</button>
          </div>}
          {verificationNotice && <p className="success-copy" role="status">{verificationNotice}</p>}
        </div>}
        <div className="form-grid">
          <label>Quiet hours start<input type="time" value={prefs.quiet_hours_start} onChange={e => setPrefs({ ...prefs, quiet_hours_start: e.target.value })} /></label>
          <label>Quiet hours end<input type="time" value={prefs.quiet_hours_end} onChange={e => setPrefs({ ...prefs, quiet_hours_end: e.target.value })} /></label>
          <label className="wide">Timezone (IANA)<input value={prefs.timezone} onChange={e => setPrefs({ ...prefs, timezone: e.target.value })} placeholder="Asia/Kolkata" /></label>
        </div>
      <label className="setting-toggle"><span>Automatic mood calls<small>When explicitly enabled, AURA may place at most one wellbeing check-in per four-hour window. Requires calling consent and a verified saved number; server switches must also be enabled.</small></span><input type="checkbox" checked={prefs.automatic_mood_calls} disabled={!prefs.calling_consent || !prefs.phone_verified || clearPhone || Boolean(phoneInput.trim())} onChange={e => setPrefs({ ...prefs, automatic_mood_calls: e.target.checked })} aria-label="Enable automatic mood calls" /></label>
      <label className="setting-toggle"><span>Deadline reminder calls<small>When enabled, due in-app reminders may trigger a phone call. Requires calling consent, a verified saved number, and server switches enabled.</small></span><input type="checkbox" checked={prefs.reminder_calls_enabled} disabled={!prefs.calling_consent || !prefs.phone_verified || clearPhone || Boolean(phoneInput.trim())} onChange={e => setPrefs({ ...prefs, reminder_calls_enabled: e.target.checked })} aria-label="Enable deadline reminder calls" /></label>
      <div className="setting-info"><b>Automatic call status</b><span>{providers?.twilio.automatic_calls_enabled ? "Backend reports automatic calling configured." : "Automatic calling is currently disabled or not fully configured on the backend. Both AURA_OUTBOUND_CALLS_ENABLED and AURA_PROACTIVE_CALLS_ENABLED must be true, with provider credentials, admin authentication, a server-only action token, and explicit saved preferences."}</span><b>Saved destination</b><span>{prefs.destination_phone_present ? `Stored with consent · ${prefs.destination_phone_masked} · ${prefs.phone_verified ? "verified" : "not verified"}` : "No destination number stored."}</span></div>
        <button disabled={saving}>{saving ? "Saving…" : "Save companion preferences"}</button>
      </form>}
    </section>
    <section className="panel settings-panel">
      <span className="eyebrow">PROVIDER STATUS</span><h2>Backend configuration</h2>
      {!providers ? <p className="muted">{loading ? "Checking configuration…" : "Provider status is unavailable."}</p> : <div className="setting-info">
        <b>Gemini</b><span>{checkLabel(providers.gemini.configured)} · server-side configuration only</span>
        <b>Twilio</b><span>{checkLabel(providers.twilio.configured)} · manual calls {providers.twilio.manual_calls_enabled ? "configured, but not live-verified" : "disabled"}; automatic calls {providers.twilio.automatic_calls_enabled ? "enabled" : "disabled"}</span>
        <b>DotVoice</b><span>{checkLabel(providers.dotvoice.configured)} · account verification {providers.dotvoice.verified ? "complete" : "not completed"}; calls disabled</span>
        <b>Gnani</b><span>{checkLabel(providers.gnani.configured)} · realtime voice bridge {providers.gnani.voice_bridge_enabled ? "enabled" : "not implemented"}</span>
        <b>Gemini Live</b><span>{providers.gemini_live.credential_present ? "Gemini credential present" : "Gemini credential missing"} · model {providers.gemini_live.model ?? "not selected"} · bridge {providers.gemini_live.bridge_implemented ? "implemented" : "not implemented"}; provider access and WSS connection {providers.gemini_live.access_verified ? "verified" : "not verified"}</span>
      </div>}
      <p className="fine-print">Configured means a value is present; it does not validate credentials or account permissions. No provider request is made by this status check.</p>
    </section>
    {error && <p className="inline-error" role="alert">{error}</p>}{notice && <p className="success-copy" role="status">{notice}</p>}
    <section className="panel settings-panel"><span className="eyebrow">THIS BROWSER</span><h2>Display preferences</h2><p className="muted">Display name and this visual reminder preference stay in this browser only.</p>
      <label>Display name<input value={displayName} onChange={e => { setName(e.target.value); setSaved(false); }} placeholder="What should we call you?" /></label>
      <label className="setting-toggle"><span><b>Reminder preference</b><small>Interface preference only; actual reminders are managed in Reminders &amp; Plans.</small></span><input type="checkbox" checked={reminders} onChange={e => { setReminders(e.target.checked); setSaved(false); }} /></label>
      <button className="btn-secondary" onClick={saveLocal}>{saved ? "Display preferences saved" : "Save display preferences"}</button>{saved && <p className="success-copy" role="status">Saved in this browser.</p>}
    </section>
  </>;
}
