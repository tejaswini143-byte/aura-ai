from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from app.gnani_bridge import _is_supported_twilio_start, greeting_text, language_code, mulaw8k_to_pcm16, raw_wave_data, tts_payload
from app.opportunity_discovery import normalize_job, search_live_opportunities


class TestLiveOpportunityDiscovery(unittest.IsolatedAsyncioTestCase):
    def test_normalize_job_keeps_source_url_and_attribution(self):
        item = normalize_job({
            "id": 123,
            "jobTitle": "Software Engineering Intern",
            "companyName": "Example Labs",
            "url": "https://jobicy.com/jobs/example-intern",
            "jobGeo": "Worldwide",
            "jobType": ["Internship"],
            "jobExcerpt": "<p>Build <b>Python</b> services.</p><script>ignore()</script>",
            "jobTags": ["Python", "FastAPI"],
            "salary": "Paid",
            "pubDate": "2026-10-25T10:00:00Z",
        })
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["source"], "Jobicy")
        self.assertEqual(item["url"], "https://jobicy.com/jobs/example-intern")
        self.assertEqual(item["type"], "Internship")
        self.assertIn("Python services", item["description"])
        self.assertNotIn("ignore()", item["description"])

    def test_rejects_untrusted_non_jobicy_urls(self):
        self.assertIsNone(normalize_job({
            "id": 9, "jobTitle": "Intern", "companyName": "Example",
            "url": "https://fake-example.test/job",
        }))

    async def test_search_filters_cached_feed_without_saving_records(self):
        feed = [
            {"id": "jobicy-1", "title": "Python Intern", "organization": "Example",
             "description": "Build backend APIs", "skills": "Python", "eligibility": "",
             "location": "Worldwide", "type": "Internship", "url": "https://jobicy.com/jobs/python",
             "remote_mode": "Remote", "source": "Jobicy", "stipend": "", "status": "Live listing",
             "is_verified": 0, "created_at": "", "published_at": "", "source_notice": ""},
            {"id": "jobicy-2", "title": "Senior Designer", "organization": "Other",
             "description": "Design systems", "skills": "Figma", "eligibility": "",
             "location": "USA only", "type": "Job", "url": "https://jobicy.com/jobs/design",
             "remote_mode": "Remote", "source": "Jobicy", "stipend": "", "status": "Live listing",
             "is_verified": 0, "created_at": "", "published_at": "", "source_notice": ""},
        ]
        with patch("app.opportunity_discovery._fetch_feed", new=AsyncMock(return_value=feed)):
            result = await search_live_opportunities("python", "india", 10)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], "jobicy-1")
        self.assertEqual(result["source"], "Jobicy")


class TestGnaniBridgeHelpers(unittest.TestCase):
    def test_language_uses_companion_preference(self):
        self.assertEqual(language_code({"preferred_language": "Telugu"}), "te-IN")
        self.assertEqual(language_code({"preferred_language": "Hindi"}), "hi-IN")
        self.assertEqual(language_code({"preferred_language": "English"}), "en-IN")

    def test_tts_requires_a_configured_voice_and_uses_telephony_audio(self):
        with patch.dict(os.environ, {"GNANI_TTS_VOICE": "Kaveri"}):
            payload = tts_payload("Namaste", {"preferred_language": "Hindi"})
        self.assertEqual(payload["voice"], "Kaveri")
        self.assertEqual(payload["model"], "timbre-v2.5")
        self.assertEqual(payload["language"], "hi-IN")
        self.assertEqual(payload["audio_config"]["container"], "mulaw")
        self.assertEqual(payload["audio_config"]["sample_rate"], 8000)
        self.assertNotIn("sample_width", payload["audio_config"])
        self.assertNotIn("encoding", payload["audio_config"])

    def test_first_greeting_uses_selected_language(self):
        self.assertIn("ఆరా", greeting_text({"preferred_language": "Telugu"}))
        self.assertIn("आरा", greeting_text({"preferred_language": "Hindi"}))
        self.assertIn("AURA", greeting_text({"preferred_language": "English"}))

    def test_twilio_start_event_must_match_saved_call(self):
        message = {"start": {
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "streamSid": "MZ123", "callSid": "CA123", "customParameters": {"callId": "call-123"},
        }}
        self.assertEqual(_is_supported_twilio_start(message, "CA123", "call-123"), ("MZ123", "CA123"))
        with self.assertRaises(ValueError):
            _is_supported_twilio_start(message, "CA999", "call-123")

    def test_mulaw_conversion_returns_pcm16(self):
        self.assertEqual(len(mulaw8k_to_pcm16(bytes([0xFF, 0x7F, 0x00]))), 6)

    def test_wave_wrapper_is_removed_without_changing_raw_audio(self):
        audio = bytes([0xFF, 0x7F, 0x00])
        self.assertEqual(raw_wave_data(audio), audio)
        fmt = b"fmt " + (16).to_bytes(4, "little") + bytes(16)
        data = b"data" + len(audio).to_bytes(4, "little") + audio
        chunks = fmt + data
        wav = b"RIFF" + (4 + len(chunks)).to_bytes(4, "little") + b"WAVE" + chunks
        self.assertEqual(raw_wave_data(wav), audio)


if __name__ == "__main__":
    unittest.main()
