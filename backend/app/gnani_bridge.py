"""Optional Gnani Prisma STT + Timbre TTS bridge for Twilio Media Streams.

Uses Gnani's documented realtime STT WebSocket and TTS REST endpoint. It is
opt-in via AURA_VOICE_ENGINE=gnani and fails closed if credentials or a voice
are not configured. The default Gemini Live bridge is left unchanged.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import struct
from typing import Any

import httpx
from fastapi import WebSocketDisconnect

from app.ai_service import GeminiService
from app.voice_bridge import decode_mulaw_sample

STT_URL = "wss://api.vachana.ai/stt/v3/stream"
TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
IDLE_TIMEOUT_SECONDS = 120
SEND_TIMEOUT_SECONDS = 10


def language_code(preferences: dict[str, Any]) -> str:
    selected = str(preferences.get("preferred_language") or "").casefold()
    return {"telugu": "te-IN", "hindi": "hi-IN", "english": "en-IN"}.get(
        selected, os.getenv("GNANI_LANGUAGE", "te-IN").strip() or "te-IN"
    )


def greeting_text(preferences: dict[str, Any]) -> str:
    """Return a short, transparent first greeting in the configured language."""
    language = str(preferences.get("preferred_language") or "English").casefold()
    if language == "telugu":
        return "నమస్కారం, నేను ఆరా. ఈరోజు మీరు ఎలా ఉన్నారు? మీతో మాట్లాడటానికి నేను ఇక్కడ ఉన్నాను."
    if language == "hindi":
        return "नमस्ते, मैं आरा हूँ। आज आप कैसा महसूस कर रहे हैं? मैं आपकी बात सुनने के लिए यहाँ हूँ।"
    return "Hi, I'm AURA. I'm here for your check-in. How are you feeling today?"


def tts_payload(text: str, preferences: dict[str, Any]) -> dict[str, Any]:
    voice = os.getenv("GNANI_TTS_VOICE", "").strip()
    if not voice:
        raise RuntimeError("GNANI_TTS_VOICE is not configured.")
    return {
        "text": text[:1800],
        "voice": voice,
        "model": "timbre-v2.5",
        "language": language_code(preferences),
        "speed": 1.0,
        "audio_config": {
            # Gnani documents container=mulaw as raw 8 kHz G.711 mu-law.
            # It forces 8 kHz; PCM sample_width/encoding are intentionally omitted.
            "sample_rate": 8000,
            "num_channels": 1,
            "container": "mulaw",
        },
    }


def mulaw8k_to_pcm16(payload: bytes) -> bytes:
    output = bytearray(len(payload) * 2)
    for index, sample in enumerate(payload):
        struct.pack_into("<h", output, index * 2, decode_mulaw_sample(sample))
    return bytes(output)


def raw_wave_data(payload: bytes) -> bytes:
    """Strip a RIFF/WAVE container if a provider returns one despite raw mu-law config."""
    if not payload.startswith(b"RIFF") or payload[8:12] != b"WAVE":
        return payload
    offset = 12
    while offset + 8 <= len(payload):
        chunk_id = payload[offset:offset + 4]
        size = int.from_bytes(payload[offset + 4:offset + 8], "little")
        start, end = offset + 8, offset + 8 + size
        if end > len(payload):
            raise ValueError("Gnani returned a truncated WAV payload.")
        if chunk_id == b"data":
            return payload[start:end]
        offset = end + (size % 2)
    raise ValueError("Gnani returned a WAV container without an audio data chunk.")


def _is_supported_twilio_start(message: dict[str, Any], expected_call_sid: str, expected_call_id: str) -> tuple[str, str]:
    start = message.get("start") or {}
    media_format = start.get("mediaFormat") or {}
    if media_format != {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}:
        raise ValueError("Twilio Media Stream audio format is unsupported.")
    stream_sid = str(start.get("streamSid") or message.get("streamSid") or "")
    call_sid = str(start.get("callSid") or "")
    if not stream_sid or not call_sid:
        raise ValueError("Twilio start event is missing its stream or call ID.")
    if expected_call_sid and call_sid != expected_call_sid:
        raise ValueError("Twilio start event does not match the expected call.")
    custom = start.get("customParameters") or {}
    if expected_call_id and str(custom.get("callId") or "") != expected_call_id:
        raise ValueError("Twilio start event does not match the expected AURA call.")
    return stream_sid, call_sid


async def run_gnani_bidirectional_bridge(
    twilio_ws: Any,
    *,
    expected_call_sid: str,
    expected_call_id: str,
    preferences: dict[str, Any],
) -> None:
    """Relay Twilio mu-law audio to Gnani STT and generated replies to Twilio."""
    api_key = os.getenv("GNANI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GNANI_API_KEY is not configured.")
    # Validate voice before opening a provider session or incurring a request.
    tts_payload("AURA voice connection test.", preferences)
    import websockets

    headers = {
        "x-api-key-id": api_key,
        "lang_code": language_code(preferences),
        "x-sample-rate": "8000",
        "x-format": "transcribe",
    }
    stream: dict[str, str] = {"stream_sid": ""}
    stream_ready = asyncio.Event()
    stopped = asyncio.Event()
    pcm_buffer = bytearray()
    conversation: list[dict[str, str]] = []
    service = GeminiService()

    async with websockets.connect(
        STT_URL,
        additional_headers=headers,
        open_timeout=10,
        close_timeout=2,
        ping_interval=20,
        max_size=1_000_000,
    ) as stt:
        first_event = await asyncio.wait_for(stt.recv(), timeout=10)
        try:
            connected = json.loads(first_event)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gnani STT returned an invalid connection event.") from exc
        if connected.get("type") != "connected":
            raise RuntimeError("Gnani STT did not confirm a ready session.")
        from app.telephony_health import mark_health
        from datetime import datetime, timezone
        mark_health(gnani_stt_at=datetime.now(timezone.utc).isoformat(), last_error=None)

        async def receive_twilio() -> None:
            while not stopped.is_set():
                try:
                    message = await asyncio.wait_for(twilio_ws.receive_json(), timeout=IDLE_TIMEOUT_SECONDS)
                except (WebSocketDisconnect, asyncio.TimeoutError):
                    stopped.set()
                    return
                event = message.get("event")
                if event == "start":
                    stream_sid, _ = _is_supported_twilio_start(message, expected_call_sid, expected_call_id)
                    stream["stream_sid"] = stream_sid
                    stream_ready.set()
                elif event == "media":
                    if not stream_ready.is_set():
                        raise ValueError("Twilio sent media before the stream start event.")
                    if str(message.get("streamSid") or "") != stream["stream_sid"]:
                        raise ValueError("Twilio media event does not match the active stream.")
                    media = message.get("media") or {}
                    if media.get("track") != "inbound":
                        continue
                    try:
                        mulaw = base64.b64decode(media.get("payload", ""), validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("Twilio sent invalid base64 audio.") from exc
                    if not mulaw or len(mulaw) > 16_000:
                        raise ValueError("Twilio audio frame size is invalid.")
                    pcm_buffer.extend(mulaw8k_to_pcm16(mulaw))
                    # Gnani requires exactly 1024 bytes (512 PCM16 samples) per frame.
                    while len(pcm_buffer) >= 1024:
                        frame = bytes(pcm_buffer[:1024])
                        del pcm_buffer[:1024]
                        await asyncio.wait_for(stt.send(frame), timeout=SEND_TIMEOUT_SECONDS)
                elif event == "stop":
                    stopped.set()
                    return

        async def speak_reply(text: str, client: httpx.AsyncClient, speech_preferences: dict[str, Any]) -> None:
            if not stream_ready.is_set():
                return
            await twilio_ws.send_json({"event": "clear", "streamSid": stream["stream_sid"]})
            payload = tts_payload(text, speech_preferences)
            response = await client.post(TTS_URL, headers={"X-API-Key-ID": api_key}, json=payload)
            response.raise_for_status()
            from app.telephony_health import mark_health
            from datetime import datetime, timezone
            mark_health(gnani_tts_at=datetime.now(timezone.utc).isoformat(), last_error=None)
            audio = raw_wave_data(response.content)
            if not audio:
                raise RuntimeError("Gnani TTS returned empty audio.")
            # The requested raw G.711 mu-law container is exactly what Twilio expects.
            for offset in range(0, len(audio), 160):
                chunk = audio[offset:offset + 160]
                if chunk:
                    await twilio_ws.send_json({
                        "event": "media",
                        "streamSid": stream["stream_sid"],
                        "media": {"payload": base64.b64encode(chunk).decode("ascii")},
                    })

        async def receive_transcripts() -> None:
            async with httpx.AsyncClient(timeout=25.0) as client:
                # Start with a spoken greeting instead of leaving the recipient
                # in silence until their first utterance is transcribed.
                try:
                    await asyncio.wait_for(stream_ready.wait(), timeout=15)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError("Twilio did not start the media stream in time.") from exc
                if stopped.is_set():
                    return
                await speak_reply(greeting_text(preferences), client, preferences)
                while not stopped.is_set():
                    raw = await asyncio.wait_for(stt.recv(), timeout=IDLE_TIMEOUT_SECONDS)
                    try:
                        event = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if event.get("type") == "error":
                        raise RuntimeError("Gnani STT reported a provider error.")
                    transcript = str(event.get("text") or "").strip() if event.get("type") == "transcript" else ""
                    if not transcript:
                        continue
                    conversation.append({"role": "user", "content": transcript[:1200]})
                    try:
                        reply = await service.generate(conversation[-8:], [], [], preferences=preferences)
                    except Exception as exc:
                        logging.getLogger(__name__).warning("AURA text response failed (%s).", type(exc).__name__)
                        reply = "I heard you. Please give me a moment and try that again."
                    conversation.append({"role": "assistant", "content": reply[:1800]})
                    from app.companion import detect_message_language
                    spoken_language = detect_message_language(transcript, str(preferences.get("preferred_language") or "English"))
                    reply_preferences = {**preferences, "preferred_language": spoken_language}
                    await speak_reply(reply, client, reply_preferences)

        tasks = (asyncio.create_task(receive_twilio()), asyncio.create_task(receive_transcripts()))
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled():
                    continue
                exception = task.exception()
                if exception:
                    raise exception
        finally:
            stopped.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
