# AURA AI backend (starter)

FastAPI backend for a voice-first personal agent combining daily planning, wellbeing check-ins, and opportunity tracking.

## Run locally

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# If activation is blocked, use .\.venv\Scripts\python.exe directly
pip install -r requirements.txt
if not exist .env copy .env.example .env  # Windows CMD; on macOS/Linux: test -e .env || cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs and http://localhost:8000/api/health.

## Real outbound calls

1. Configure a telephony account and put credentials in `.env`; never expose them in frontend code.
2. Publish the backend behind a public HTTPS URL and set `AURA_PUBLIC_BASE_URL`.
3. Set `TWILIO_FROM_NUMBER` to a number enabled for your account and destination.
4. In the dashboard, only request a call after the recipient has explicitly consented.

**India note:** Twilio's current India voice guidance says outbound calls to Indian numbers can only be made from international (non-Indian) numbers, and calls must comply with consent and local telecom rules. Check current provider terms and local requirements before real calls. A telephony provider with compliant India calling may be more suitable for an India-first deployment.

## Important implementation status

- Implemented: tasks, opportunities, call records, outbound Twilio call creation, provider callbacks, consent gate, simple voice prompt, speech response capture.
- The outbound call's TwiML uses a bidirectional Twilio `<Connect><Stream>` and `/api/telephony/stream/{call_id}` to relay live call audio to Gemini Live. The server converts Twilio G.711 mu-law/8 kHz mono to Gemini PCM16/16 kHz, and Gemini PCM16/24 kHz back to Twilio mu-law/8 kHz. Barge-in clears Twilio's playback buffer. This code path is not provider-verified until real credentials, model access, and a public WSS endpoint are configured and tested.
- The current speech capture uses Twilio's built-in speech recognition as a temporary bootstrap; it should be replaced or augmented with Gnani Prisma for the challenge's core demo.
- The local SQLite-backed scheduler and in-app reminder queue are implemented. Live opportunity discovery and multi-user identity remain unavailable. Settings and sensitive call/verification actions use a single-admin, SQLite-backed browser session with CSRF checks.
- Do not use for emergency monitoring or medical decisions. Care check-ins are not a substitute for professional or emergency services.


## Gemini assistant and automation

- The server uses Google's official `google-genai` Python SDK. Set `GEMINI_API_KEY` in `backend/.env`; the key is server-only and must never use a `NEXT_PUBLIC_` variable.
- Configured text model: `gemini-3.5-flash-lite` (`GEMINI_MODEL`). The AI chat uses saved AURA tasks and opportunities as read-only context and returns suggestions; no application action tools are exposed.
- Copy the placeholder from `.env.example` only if you need a new local `.env`; do not replace an existing `.env`. Set the key locally and restart the backend.
- `/api/automation/settings`, `/api/reminders`, `/api/tasks/{task_id}/reminders`, and `/api/automation/plan/*` manage persistent SQLite-backed in-app reminders and once-per-day deterministic plans. The background scheduler only moves due reminders into the in-app ready queue; it does not send calls, SMS, or email.
- `/api/opportunities/discovery` reports that live discovery is unavailable until a legitimate provider and required credentials are configured. `/api/opportunities` continues to return the user's saved records.

## Companion settings and current feature boundaries

### Admin sign-in and phone verification setup

1. Keep the existing `backend/.env`; add only the settings you need. Set `AURA_ADMIN_PASSWORD` to a unique, randomly generated value of at least 16 characters. Keep it server-side and never commit it or put it in a `NEXT_PUBLIC_*` variable.
2. Set `AURA_ALLOWED_ORIGINS` to the exact frontend origins (scheme and host, including port; comma-separated, with no paths). Local defaults allow ports 3000, 3001, and 3002.
3. Set `AURA_ENVIRONMENT=production` when deployed. This enables the Secure cookie flag. Use HTTPS for production sign-in. Sessions are opaque and stored server-side, expire after eight hours, and require CSRF headers for mutations. `AURA_SESSION_COOKIE_SAMESITE` defaults to `strict`; only use `none` for a cross-site frontend/backend deployment, with Secure cookies and exact `AURA_ALLOWED_ORIGINS` configured.
4. For phone verification, configure `TWILIO_VERIFY_SERVICE_SID` and existing Twilio credentials in `backend/.env`. Keep `AURA_PHONE_VERIFICATION_ENABLED=false` until you intentionally want a user-confirmed verification SMS. No SMS is sent merely by starting the backend.
5. Sign in to Settings, save a destination with storage consent, separately consent to one verification SMS, then click **Send verification code**. The action token is not needed by the browser. `AURA_ACTIONS_API_TOKEN` is optional for trusted server-to-server clients only and must remain server-side.

- Missing or short `AURA_ADMIN_PASSWORD` disables sign-in and all sensitive call/verification actions. There is no default password.
- The Settings UI never stores the password, session ID, verification code, or action token in browser storage. The session cookie is HttpOnly; the CSRF token is held in memory only.
- Keep `AURA_OUTBOUND_CALLS_ENABLED=false`; verifying a phone number does not enable calls.

- `GET/PUT /api/companion/preferences` stores the single-workspace companion settings in SQLite. Phone numbers are only accepted with the explicit `store_phone_with_consent` flag and are returned masked. Saving preferences alone cannot mark a number verified.
- `POST /api/companion/chat` is a separate, non-persistent companion conversation endpoint. It detects simple mood cues in English, Hindi, Telugu and common Romanized Telugu phrases, checks a limited set of immediate-risk phrases, uses Gemini with the selected style/language when available, and falls back to brief supportive text when the provider is unavailable. Detection and generation are best-effort, not clinical or certified. Language instructions prioritize the latest clearly identifiable English, Hindi, or Telugu message. The optional **Boyfriend** style is a fictional conversational tone only; the AI must not claim to be a real partner or encourage emotional dependence. It does not retain messages or automatically place calls.
- `GET /api/auth/session`, `POST /api/auth/login`, and `POST /api/auth/logout` manage the admin session. Cookies are HttpOnly, SameSite=Strict by default, and Secure when `AURA_ENVIRONMENT=production` (or when SameSite=None). State-changing browser requests require the in-memory CSRF token returned by login/session. Login is origin-allowlisted and limited to five failed attempts per client in a 15-minute window. The session expires after eight hours. `/api/providers/status` returns configuration booleans only; it does not validate a credential against a provider.
- The optional `AURA_ACTIONS_API_TOKEN` is a backend-only bearer credential for trusted server-to-server clients. It is never returned by an endpoint and is not used by the browser. Sensitive actions still fail closed unless `AURA_ADMIN_PASSWORD` is configured. Do not place the action token in a `NEXT_PUBLIC_*` variable.
- Reminders remain persistent in-app reminders in the existing SQLite scheduler. Call/SMS reminder delivery is intentionally unavailable. The scheduler uses a local polling thread and is not protected against multiple backend worker processes; run one backend worker for this prototype.
- The Twilio stream handshake validates `X-Twilio-Signature`; call and stream IDs are matched before audio relay. Gemini Live errors end the stream and produce a plain disconnect message, not fabricated AI audio. No provider requests or real calls were made for this code change.
- `GEMINI_MODEL` remains the text-chat model; `GEMINI_LIVE_MODEL` selects the separate Live API model (`gemini-3.8-live`, per the current Google GenAI SDK guide). A key being present does not prove Live model access. The existing outbound master switch remains off by default.
- Destination verification uses Twilio Verify only when `AURA_PHONE_VERIFICATION_ENABLED=true`, `TWILIO_VERIFY_SERVICE_SID`, Twilio credentials, and `AURA_ADMIN_PASSWORD` are configured. Settings requires sign-in, a saved destination with storage consent, a separate explicit SMS consent, and a deliberate “Send verification code” click. The check route marks the number verified only after Twilio returns approved. AURA stores a hashed phone lookup key and verification state, never the code; it enforces a 60-second resend cooldown, 10-minute local expiry, and five-check limit. Tests mock Twilio. No verification SMS was sent during implementation.
- Outbound call creation fails closed unless `AURA_OUTBOUND_CALLS_ENABLED=true`, the server has Twilio credentials and a public HTTPS callback URL, per-request consent is supplied, a verified destination exists, admin authentication or the optional backend action token is valid, and an idempotency key is supplied. Cookie-authenticated requests also require CSRF validation. Requests are bounded by `AURA_MAX_CALLS_PER_DAY`, `AURA_MAX_CALL_DURATION_SECONDS`, and saved quiet hours. Twilio callbacks are rejected unless their `X-Twilio-Signature` validates against the configured public URL and server auth token. The global outbound switch remains off.
- Companion preference storage includes the calling-consent timestamp and an opt-in transcript-storage preference. Automatic mood-triggered calls and reminder phone calls are explicitly unavailable; the supported scheduler delivers in-app reminders only. The Gemini text companion can answer mood-related messages, but the backend now returns a clear unavailability reason instead of implying a phone call can be offered.
- Automated tests use temporary database paths; do not point test commands at the project database. This change's source and SQLite backup is under `project-backups/secure-settings-before-20261025-111012/`.
- For immediate danger or self-harm language, the companion endpoint returns supportive guidance encouraging contact with a trusted person or local emergency services. The phrase check is limited, not emergency detection or monitoring, and AURA is not a substitute for human/professional help.

Windows Command Prompt startup (two windows):

```bat
cd /d C:\Users\Tejaswini\Downloads\aura-ai-starter-backup\backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bat
cd /d C:\Users\Tejaswini\Downloads\aura-ai-starter-backup\frontend
npm run dev -- --hostname 127.0.0.1 --port 3001
```

Open `http://localhost:3001`. The frontend API URL remains configurable with `NEXT_PUBLIC_AURA_API_URL` and defaults to `http://localhost:8000`.
