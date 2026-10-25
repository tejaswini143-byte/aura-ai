"""Twilio Media Streams <-> Gemini Live audio bridge.

Twilio sends G.711 mu-law/8 kHz mono; Gemini Live accepts PCM16/16 kHz and
returns PCM16/24 kHz. This module contains the protocol/audio relay only. It
never initiates calls and receives an already-authenticated WebSocket/session.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import struct
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import WebSocketDisconnect

logger = logging.getLogger(__name__)

LIVE_MODEL_DEFAULT = "gemini-3.8-live"
LIVE_IDLE_TIMEOUT_SECONDS = 120
LIVE_CONNECT_TIMEOUT_SECONDS = 25
LIVE_SEND_TIMEOUT_SECONDS = 10
MAX_TWILIO_FRAME_BYTES = 16_000


def decode_mulaw_sample(value: int) -> int:
    """Decode one G.711 mu-law octet into signed linear PCM16."""
    value = (~value) & 0xFF
    magnitude = ((value & 0x0F) << 3) + 0x84
    magnitude <<= (value & 0x70) >> 4
    return (0x84 - magnitude) if value & 0x80 else (magnitude - 0x84)


def encode_mulaw_sample(sample: int) -> int:
    """Encode one signed linear PCM16 sample as a G.711 mu-law octet."""
    sample = max(-32635, min(32635, int(sample)))
    mask = 0x7F if sample < 0 else 0xFF
    if sample < 0:
        sample = -sample
    sample += 0x84
    exponent = 7
    for bit in (0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100):
        if sample & bit:
            break
        exponent -= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (exponent << 4 | mantissa) ^ mask


def mulaw_8k_to_pcm16_16k(payload: bytes) -> bytes:
    """Decode mu-law/8 kHz mono and linearly upsample by 2 for Gemini."""
    output = bytearray(len(payload) * 4)
    offset = 0
    samples = [decode_mulaw_sample(value) for value in payload]
    for index, current in enumerate(samples):
        following = samples[index + 1] if index + 1 < len(samples) else current
        midpoint = (current + following) // 2
        struct.pack_into("<hh", output, offset, current, midpoint)
        offset += 4
    return bytes(output)


class Pcm24kToMulaw8k:
    """Stateful 3:1 decimator and mu-law encoder for arbitrary PCM chunks."""

    def __init__(self) -> None:
        self._pending = b""

    def convert(self, pcm16: bytes) -> bytes:
        data = self._pending + pcm16
        sample_bytes = len(data) - (len(data) % 2)
        usable = data[:sample_bytes]
        samples = struct.unpack(f"<{sample_bytes // 2}h", usable) if usable else ()
        complete = len(samples) - (len(samples) % 3)
        self._pending = usable[complete * 2:] + data[sample_bytes:]
        output = bytearray(complete // 3)
        for index in range(0, complete, 3):
            # A short box filter before decimation limits aliasing; output is
            # one sample per three 24 kHz PCM samples (8 kHz telephony audio).
            average = (samples[index] + samples[index + 1] + samples[index + 2]) // 3
            output[index // 3] = encode_mulaw_sample(average)
        return bytes(output)


def live_model_name() -> str:
    return os.getenv("GEMINI_LIVE_MODEL", LIVE_MODEL_DEFAULT).strip() or LIVE_MODEL_DEFAULT


@asynccontextmanager
async def open_gemini_live_session(
    *, api_key: str, system_instruction: str, model: str | None = None
) -> AsyncIterator[Any]:
    """Open the documented asynchronous google-genai Live API session."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    try:
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_instruction,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        async with asyncio.timeout(LIVE_CONNECT_TIMEOUT_SECONDS):
            async with client.aio.live.connect(model=model or live_model_name(), config=config) as session:
                yield session
    finally:
        await client.aio.aclose()


async def relay_twilio_media_to_gemini(websocket: Any, session: Any, stream: dict[str, str], ready: asyncio.Event) -> None:
    while True:
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=LIVE_IDLE_TIMEOUT_SECONDS)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            return
        event = message.get("event")
        if event == "start":
            start = message.get("start") or {}
            media_format = start.get("mediaFormat") or {}
            if media_format != {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}:
                raise ValueError("Twilio Media Stream audio format is unsupported.")
            stream_sid = str(start.get("streamSid") or message.get("streamSid") or "")
            call_sid = str(start.get("callSid") or "")
            if not stream_sid or not call_sid:
                raise ValueError("Twilio start event is missing its stream or call ID.")
            expected_call_sid = stream.get("call_sid")
            if expected_call_sid and call_sid != expected_call_sid:
                raise ValueError("Twilio start event does not match the expected call.")
            expected_call_id = stream.get("call_id")
            custom_parameters = start.get("customParameters") or {}
            if expected_call_id and str(custom_parameters.get("callId") or "") != expected_call_id:
                raise ValueError("Twilio start event does not match the expected AURA call.")
            stream["stream_sid"] = stream_sid
            ready.set()
        elif event == "media":
            if not ready.is_set():
                raise ValueError("Twilio sent media before the stream start event.")
            media = message.get("media") or {}
            if media.get("track") != "inbound":
                continue
            if str(message.get("streamSid") or "") != stream["stream_sid"]:
                raise ValueError("Twilio media event does not match the active stream.")
            try:
                mulaw_audio = base64.b64decode(media.get("payload", ""), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Twilio sent an invalid base64 audio frame.") from exc
            if not mulaw_audio or len(mulaw_audio) > MAX_TWILIO_FRAME_BYTES:
                raise ValueError("Twilio audio frame size is invalid.")
            from google.genai import types
            await asyncio.wait_for(
                session.send_realtime_input(
                    audio=types.Blob(data=mulaw_8k_to_pcm16_16k(mulaw_audio), mime_type="audio/pcm;rate=16000")
                ),
                timeout=LIVE_SEND_TIMEOUT_SECONDS,
            )
        elif event == "stop":
            return
        # connected, mark, and inbound DTMF events do not contain audio.


async def relay_gemini_audio_to_twilio(websocket: Any, session: Any, stream: dict[str, str], ready: asyncio.Event) -> None:
    await asyncio.wait_for(ready.wait(), timeout=LIVE_CONNECT_TIMEOUT_SECONDS)
    converter = Pcm24kToMulaw8k()
    responses = session.receive().__aiter__()
    while True:
        try:
            response = await asyncio.wait_for(anext(responses), timeout=LIVE_IDLE_TIMEOUT_SECONDS)
        except StopAsyncIteration:
            return
        content = getattr(response, "server_content", None)
        if content is None:
            continue
        if getattr(content, "interrupted", False) and stream.get("stream_sid"):
            await asyncio.wait_for(websocket.send_json({"event": "clear", "streamSid": stream["stream_sid"]}), timeout=LIVE_SEND_TIMEOUT_SECONDS)
        turn = getattr(content, "model_turn", None)
        for part in getattr(turn, "parts", None) or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is None:
                continue
            mime_type = (getattr(inline_data, "mime_type", None) or "").lower()
            if not mime_type.startswith("audio/pcm") or "rate=24000" not in mime_type:
                raise ValueError("Gemini Live returned audio outside the expected PCM16/24 kHz format.")
            audio = inline_data.data
            if isinstance(audio, str):
                audio = base64.b64decode(audio)
            mulaw_audio = converter.convert(bytes(audio))
            # Send 20 ms packets without WAV/container headers. Twilio buffers
            # these in order and the clear event above implements barge-in.
            for offset in range(0, len(mulaw_audio), 160):
                chunk = mulaw_audio[offset:offset + 160]
                if chunk:
                    await asyncio.wait_for(websocket.send_json({
                        "event": "media",
                        "streamSid": stream["stream_sid"],
                        "media": {"payload": base64.b64encode(chunk).decode("ascii")},
                    }), timeout=LIVE_SEND_TIMEOUT_SECONDS)


async def run_bidirectional_bridge(websocket: Any, session: Any, expected_call_sid: str = "", expected_call_id: str = "") -> None:
    stream = {"call_sid": expected_call_sid, "call_id": expected_call_id}
    ready = asyncio.Event()
    input_task = asyncio.create_task(relay_twilio_media_to_gemini(websocket, session, stream, ready))
    output_task = asyncio.create_task(relay_gemini_audio_to_twilio(websocket, session, stream, ready))
    tasks = (input_task, output_task)
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                continue
            exception = task.exception()
            if exception:
                raise exception
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def twilio_websocket_signature_valid(url: str, signature: str, auth_token: str) -> bool:
    """Validate the WSS upgrade signature; Twilio documents trailing slash fallback."""
    if not url.startswith("wss://") or not signature or not auth_token:
        return False
    from twilio.request_validator import RequestValidator

    validator = RequestValidator(auth_token)
    return validator.validate(url, {}, signature) or validator.validate(url + "/", {}, signature)
