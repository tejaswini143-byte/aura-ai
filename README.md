# AURA AI — Opportunity Action Agent

AURA is a voice-first opportunity action agent that helps students follow through on internships, scholarships, hackathons and deadlines. The prototype connects three modules:
- **LifePilot:** goals, tasks, daily plan and replanning
- **Care:** opt-in scheduled voice check-ins
- **Opportunity Radar:** saved internships, scholarships, hackathons and deadlines
- **Next Move briefing:** a transparent, rule-based suggestion from open tasks and upcoming deadlines

This repository includes a Next.js dashboard and a FastAPI backend with SQLite persistence, task/opportunity APIs, a consent-gated Twilio outbound-call flow, a live remote opportunity feed, and an optional Gnani Prisma STT + Timbre TTS bridge. The Gemini Live bridge remains the default. Real provider calls still require valid account credentials, a public HTTPS/WSS backend, explicit consent, and an end-to-end test.

## Run the backend

```bash
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS/Linux: . .venv/bin/activate && pip install -r requirements.txt
if [ ! -e .env ]; then cp .env.example .env; fi
uvicorn app.main:app --reload --port 8000
```

Windows PowerShell (creates a template only when no `.env` exists): `if (-not (Test-Path .env)) { Copy-Item .env.example .env }`.

## Run the frontend

In a second terminal:

```bash
cd frontend
npm install
# Copy .env.local.example to .env.local if desired; default API URL is localhost:8000
npm run dev
```

Open http://localhost:3000. API docs: http://localhost:8000/docs.

## Implementation roadmap

1. **Foundation (included):** dashboard, API health, tasks, opportunity records, call log, consent gate, and a rule-based `/api/briefing` next-action endpoint.
2. **Gnani voice:** optional Prisma STT + Timbre v2.5 TTS bridge is implemented behind `AURA_VOICE_ENGINE=gnani`; configure the server-only API key and a supported TTS voice before testing. The bridge has offline protocol tests but is not yet provider-verified.
3. **Call conversation loop:** the Gnani bridge supports multi-turn transcript → Gemini text response → TTS audio. Live interruption, provider-specific edge cases, call outcomes and deployment behavior still require an end-to-end phone test.
4. **Planning engine:** user goals, time blocks, priorities, conflicts, replan proposals and explicit user approval before changing calendar/task data.
5. **Opportunity Radar:** saved records plus a live remote-job feed from Jobicy. Results retain original source links and attribution and require the user to save a listing before it enters the personal Radar. Eligibility and availability must be confirmed on the source page.
6. **Scheduling:** durable background jobs, timezone-aware reminders, retry limits, quiet hours and opt-out controls.
7. **Care module:** opt-in check-in cadence, user-selected trusted contact and transparent escalation settings. Never position it as emergency or medical monitoring.
8. **Security:** authentication, per-user authorization, secret management, request validation, webhook signature verification, rate limiting, audit log and deletion/export controls.
9. **Evaluation:** test Telugu-English and other supported speech, noisy phone audio, task rescheduling correctness, call success rate, false alerts and user satisfaction.
10. **Deploy:** deploy frontend and API, configure HTTPS callbacks, set secrets in host environment, record a demo with synthetic data and explicit consent.

## Important telephony note for India

Twilio's current India voice guidelines state that outbound calls to Indian numbers can only be made from international (non-Indian) numbers. Review its latest terms and local telecom rules before testing. A provider with appropriate India outbound voice support may be a better fit. Do not call people without consent; do not use this app for emergencies.


## Deployment

- The root `netlify.toml` configures the Next.js frontend build from `frontend/`. In Netlify site settings, set `NEXT_PUBLIC_AURA_API_URL` to the public HTTPS URL of the backend.
- Deploy the FastAPI backend separately. Set `AURA_PUBLIC_BASE_URL` to its public HTTPS origin and add the exact frontend origin to `AURA_ALLOWED_ORIGINS`.
- Store Gemini, Twilio, and Gnani credentials only in the backend host's secret/environment settings. Never put them in Git or `NEXT_PUBLIC_*` variables.
- Both `AURA_OUTBOUND_CALLS_ENABLED` and `AURA_PROACTIVE_CALLS_ENABLED` default to `false`. Keep them off until provider status, verified destination, consent, HTTPS callbacks, and a manual test call have been confirmed.
- See [backend deployment and integration notes](backend/README.md) for health checks and the exact environment checklist.
