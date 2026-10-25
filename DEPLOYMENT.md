# AURA deployment checklist

## 1. Deploy the backend on Render

1. Merge the reviewed Render Blueprint PR, then open the Render dashboard.
2. Choose **New + → Blueprint** and connect the GitHub repository `tejaswini143-byte/aura-ai` on branch `main`.
3. Render reads `render.yaml`. Review the selected plan and charges before creating the resources.
4. The blueprint creates a paid Starter web service and a 1 GB persistent disk. The disk is intentional: AURA's SQLite data must survive restarts and redeploys.
5. Wait for the service to deploy. Copy its public HTTPS URL, such as `https://<your-service>.onrender.com`.

## 2. Set private backend environment variables

In the Render service's **Environment** page, add/update these values. Do not put secrets in GitHub or frontend variables.

Required for the configured Gnani voice path:
- `AURA_PUBLIC_BASE_URL`: the exact Render HTTPS origin, with no trailing slash.
- `AURA_ALLOWED_ORIGINS`: the exact public frontend origin (add the frontend later if it is not deployed yet).
- `GEMINI_API_KEY`: server-side Gemini API key; AURA uses Gemini for generated replies.
- `GNANI_API_KEY`: Gnani API key.
- `GNANI_TTS_VOICE`: a valid Timbre v2.5 voice identifier for the selected language, copied from the Gnani voice catalog.
- `GNANI_LANGUAGE`: `te-IN` (or the desired supported fallback language).
- `AURA_VOICE_ENGINE`: `gnani`.

The blueprint generates `AURA_ADMIN_PASSWORD` and `AURA_ACTIONS_API_TOKEN`. Save these securely using your password manager; do not share them in chat.

Twilio is optional until its account is confirmed. Only after checking the account, add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, and (if using phone verification) `TWILIO_VERIFY_SERVICE_SID`. Never guess values or paste tokens into chat. A caller number and a destination number are different things.

After changing environment variables, save and let Render redeploy.

## 3. Verify the backend

Open these URLs using your actual Render origin:
- `/api/health`
- `/api/providers/status`
- `/api/opportunities/discovery`

A healthy HTTP response confirms the service is reachable; it does **not** prove that Gnani, Gemini, or Twilio credentials work. Check provider status for observed connections. Do not enable either call switch yet.

## 4. Safety defaults

Keep `AURA_OUTBOUND_CALLS_ENABLED=false`, `AURA_PROACTIVE_CALLS_ENABLED=false`, and `AURA_PHONE_VERIFICATION_ENABLED=false` until the relevant provider setup and a manual, explicitly consented end-to-end test are confirmed. Twilio calling to Indian mobile numbers may be restricted by current provider terms; check account-specific eligibility and local requirements first.

## 5. Frontend

The Next.js frontend is deployed separately. Once it has a public HTTPS URL, set `AURA_ALLOWED_ORIGINS` on the backend to that exact origin and set `NEXT_PUBLIC_AURA_API_URL` in the frontend host to the Render backend origin. Never put provider keys in `NEXT_PUBLIC_*` variables.
