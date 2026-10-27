"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

const prompts = ["What’s one thing you’re glad you did today?", "What would feel supportive right now?", "What is one small thing you’d like to focus on?"];
type ChatMessage = { role: "user" | "assistant"; content: string };
type CompanionResponse = { reply: string; provider: "gemini" | "fallback" | "safety_fallback"; mood: string | null; conversation_saved: boolean; automatic_call_triggered: boolean; automatic_call_status?: string | null; call_offer: boolean; call_offer_text?: string | null; call_unavailable_reason?: string | null };

export default function Wellbeing() {
  const [mood, setMood] = useState("");
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [companionResult, setCompanionResult] = useState<CompanionResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const value = localStorage.getItem("aura-wellbeing-checkin");
    if (value) {
      try { const prior = JSON.parse(value); setMood(prior.mood || ""); setNote(prior.note || ""); }
      catch { localStorage.removeItem("aura-wellbeing-checkin"); }
    }
  }, []);

  function saveCheckIn() {
    localStorage.setItem("aura-wellbeing-checkin", JSON.stringify({ mood, note, at: new Date().toISOString() }));
    setSaved(true);
  }

  function clearCheckIn() {
    localStorage.removeItem("aura-wellbeing-checkin");
    setMood(""); setNote(""); setSaved(false);
  }

  async function sendToCompanion(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    const next = [...messages, { role: "user" as const, content: text }];
    setMessages(next); setInput(""); setError(""); setCompanionResult(null); setBusy(true);
    try {
      const result = await api<CompanionResponse>("/api/companion/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, history: messages.slice(-8) }),
      });
      setMessages([...next, { role: "assistant", content: result.reply }]);
      setCompanionResult(result);
    } catch (e) {
      setMessages(messages);
      setInput(text);
      setError(e instanceof Error ? e.message : "AURA could not reply. Please retry.");
    } finally { setBusy(false); }
  }

  return <>
    <section className="wellbeing-hero"><span className="eyebrow">A MOMENT FOR YOU</span><h2>How are you arriving<br />to today?</h2><p>Take a breath. There’s no right answer, and you can skip this whenever you like.</p></section>
    <section className="panel checkin-panel">
      <h2>Choose what feels closest</h2>
      <div className="mood-grid" role="group" aria-label="How are you feeling today?">{[["calm", "Calm", "◌"], ["good", "Good", "☀"], ["mixed", "A bit mixed", "◐"], ["low", "Low energy", "☁"]].map(([value, label, icon]) => <button type="button" key={value} className={`mood-option ${mood === value ? "chosen" : ""}`} aria-pressed={mood === value} onClick={() => { setMood(value); setSaved(false); }}><span>{icon}</span>{label}</button>)}</div>
      <label className="journal-label" htmlFor="checkin-note">{prompts[new Date().getDate() % prompts.length]}</label>
      <textarea id="checkin-note" rows={4} placeholder="A few words for yourself (optional)" value={note} onChange={e => { setNote(e.target.value); setSaved(false); }} />
      <div className="checkin-footer"><span className="fine-print">Stored only in this browser. Not a medical service.</span><div className="opp-card-actions"><button disabled={!mood} onClick={saveCheckIn}>{saved ? "Check-in saved" : "Save check-in"}</button>{(mood || note) && <button className="btn-secondary" onClick={clearCheckIn}>Clear saved check-in</button>}</div></div>
      {saved && <p className="success-copy" role="status">Thanks for checking in with yourself.</p>}
    </section>
    <section className="panel assistant-panel">
      <div className="section-head"><div><span className="eyebrow">PRIVATE, EPHEMERAL CHAT</span><h2>Talk with AURA</h2></div></div>
      <p className="muted">Share what is on your mind. This chat is not stored by the backend. AURA is an AI, not a clinician or emergency service.</p>
      {messages.length > 0 && <div className="chat-messages" aria-live="polite">{messages.map((message, index) => <article className={`chat-message ${message.role}`} key={index}><b>{message.role === "user" ? "You" : "AURA · AI companion"}</b><p>{message.content}</p></article>)}{busy && <p className="muted" role="status">AURA is replying…</p>}</div>}
      {companionResult && <div className="notice-banner" role="status"><b>{companionResult.provider === "gemini" ? "Gemini response" : companionResult.provider === "safety_fallback" ? "Support guidance" : "Supportive fallback"}</b><span>{companionResult.conversation_saved ? "Conversation saved" : "Conversation not saved"}. {companionResult.automatic_call_triggered ? `AURA submitted a call request (${companionResult.automatic_call_status || "submitted"}); this does not confirm an answer or working live audio.` : "No automatic call was triggered."}{companionResult.mood ? ` Best-effort mood cue: ${companionResult.mood}.` : ""}</span>{companionResult.call_unavailable_reason && <span>{companionResult.call_unavailable_reason}</span>}</div>}
      {error && <p role="alert" className="inline-error">{error} Edit the message if needed, then press Send to retry.</p>}
      <form className="chat-composer" onSubmit={sendToCompanion}><label className="sr-only" htmlFor="wellbeing-companion-input">Message AURA</label><textarea id="wellbeing-companion-input" value={input} onChange={e => setInput(e.target.value)} maxLength={4000} rows={2} placeholder="I feel low today…"/><button disabled={busy || !input.trim()}>{busy ? "Sending…" : "Send"}</button></form>
    </section>
    <aside className="gentle-note"><b>A gentle reminder</b><p>This space is for reflection and personal planning. It doesn’t provide medical advice or monitor emergencies.</p></aside>
  </>;
}
