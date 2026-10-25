# AURA AI — Opportunity Action Agent

AURA is a voice-first opportunity action agent that helps students follow through on internships, scholarships, hackathons and deadlines. The prototype connects three modules:
- **LifePilot:** goals, tasks, daily plan and replanning
- **Care:** opt-in scheduled voice check-ins
- **Opportunity Radar:** saved internships, scholarships, hackathons and deadlines
- **Next Move briefing:** a transparent, rule-based suggestion from open tasks and upcoming deadlines

This starter includes a Next.js dashboard and a FastAPI backend with SQLite persistence, task/opportunity APIs, and a consent-gated Twilio outbound-call flow. Gnani integration is intentionally marked as the next implementation step because the account credentials and chosen official API interface must be verified before wiring in live audio.

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
2. **Gnani voice:** integrate Prisma STT and Timbre TTS using the credentials and endpoint/version available in the user's Gnani challenge account. For phone audio, record or stream the call audio, send it through Prisma, generate a response with the agent brain, synthesize it with Timbre, and play the generated audio through the telephony provider.
3. **Call conversation loop:** handle multiple turns, silence, interruptions, timeout, call outcomes and transcript review.
4. **Planning engine:** user goals, time blocks, priorities, conflicts, replan proposals and explicit user approval before changing calendar/task data.
5. **Opportunity Radar:** official sources or user-saved links, deadline extraction with source URLs, eligibility checks and reminders. Never invent deadlines or submit applications without approval.
6. **Scheduling:** durable background jobs, timezone-aware reminders, retry limits, quiet hours and opt-out controls.
7. **Care module:** opt-in check-in cadence, user-selected trusted contact and transparent escalation settings. Never position it as emergency or medical monitoring.
8. **Security:** authentication, per-user authorization, secret management, request validation, webhook signature verification, rate limiting, audit log and deletion/export controls.
9. **Evaluation:** test Telugu-English and other supported speech, noisy phone audio, task rescheduling correctness, call success rate, false alerts and user satisfaction.
10. **Deploy:** deploy frontend and API, configure HTTPS callbacks, set secrets in host environment, record a demo with synthetic data and explicit consent.

## Important telephony note for India

Twilio's current India voice guidelines state that outbound calls to Indian numbers can only be made from international (non-Indian) numbers. Review its latest terms and local telecom rules before testing. A provider with appropriate India outbound voice support may be a better fit. Do not call people without consent; do not use this app for emergencies.
