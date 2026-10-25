# AURA deployment checklist

## 1. Deploy the backend on Render (free testing)

1. Open Render and choose **New + → Blueprint**.
2. Use repository `tejaswini143-byte/aura-ai`, branch `main`, and Blueprint Path `render.yaml`.
3. Name the Blueprint (for example, `aura-ai-free-testing`) and review the detected resources.
4. The free Blueprint is intended for testing and should not require a paid web-service plan. If Render still requests payment, stop before adding a card and review the selected service plan.
5. Copy the public HTTPS service URL after deployment, such as `https://<your-service>.onrender.com`.

**Free-tier limitations:** the service can spin down when idle and take time to wake up. The local SQLite database is stored at `/tmp/aura.db` and is ephemeral; tasks, opportunities, preferences, and call metadata may be lost on restart/redeploy. Do not rely on this for important data. A persistent database/disk may require a paid option.

## 2. Set private backend environment variables

In the Render service's **Environment** page, add these values. Do not put secrets in GitHub or frontend variables.

Required for the configured Gnani voice path:
- `AURA_PUBLIC_BASE_URL`: the exact Render HTTPS origin, with no trailing slash.
- `AURA_ALLOWED_ORIGINS`: the exact public frontend origin (for example, `https://stunning-kitten-f76100.netlify.app` if that is still the deployed site).
- `GEMINI_API_KEY`: server-side Gemini API key; AURA uses Gemini for generated replies.
- `GNANI_API_KEY`: Gnani API key.
- `GNANI_TTS_VOICE`: a valid Timbre v2.5 voice identifier for the selected language, copied from the Gnani voice catalog.
- `GNANI_LANGUAGE`: `te-IN` (or the desired supported fallback language).
- `AURA_VOICE_ENGINE`: `gnani`.

The Blueprint generates `AURA_ADMIN_PASSWORD` and `AURA_ACTIONS_API_TOKEN`. Save them securely; do not share them in chat.

Twilio is optional until its account is confirmed. Only after checking the account, add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, and (if using phone verification) `TWILIO_VERIFY_SERVICE_SID`. Never guess values or paste tokens into chat. A caller number and a destination number are different things.

After changing environment variables, save and let Render redeploy. Do not expose secret keys through any `NEXT_PUBLIC_*` variable.

## 3. Verify the backend

Open these URLs using your actual Render origin:
- `/api/health`
- `/api/providers/status`
- `/api/opportunities/discovery`

A healthy HTTP response confirms the service is reachable; it does **not** prove that Gnani, Gemini, or Twilio credentials work. Check provider status for observed connections. Do not enable either call switch yet.

## 4. Safety defaults

Keep `AURA_OUTBOUND_CALLS_ENABLED=false`, `AURA_PROACTIVE_CALLS_ENABLED=false`, and `AURA_PHONE_VERIFICATION_ENABLED=false` until provider setup and a manual, explicitly consented end-to-end test are confirmed. Twilio calling to Indian mobile numbers may be restricted by current provider terms; check account-specific eligibility and local requirements first.

## 5. Frontend

The Next.js frontend remains on Netlify. Once the backend has a public HTTPS URL:
- Set `AURA_ALLOWED_ORIGINS` on Render to the exact Netlify origin.
- Set `NEXT_PUBLIC_AURA_API_URL` in Netlify's environment settings to the Render backend origin (no trailing slash).
- Trigger a Netlify redeploy after changing its environment variables.

The existing Netlify URL mentioned in earlier setup was `https://stunning-kitten-f76100.netlify.app/`; verify it is still the current deployment URL before using it.
