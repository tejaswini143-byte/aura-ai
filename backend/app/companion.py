from __future__ import annotations

import re
from typing import Any

MOOD_TERMS = {
    "sad": ("sad", "down", "unhappy", "low", "उदास", "దుఃఖంగా", "బాధగా"),
    "lonely": ("lonely", "alone", "isolated", "अकेला", "ఒంటరిగా"),
    "anxious": ("anxious", "anxiety", "worried", "nervous", "चिंता", "ఆందోళన"),
    "stressed": ("stressed", "overwhelmed", "pressure", "तनाव", "ఒత్తిడి"),
    "happy": ("happy", "great", "good today", "खुश", "సంతోషంగా"),
}
RISK_TERMS = (
    "kill myself", "end my life", "suicide", "self harm", "hurt myself",
    "आत्महत्या", "खुद को नुकसान", "నా ప్రాణం తీసుకోవాలి", "ఆత్మహత్య",
)


def detect_mood(message: str) -> str | None:
    text = message.casefold()
    for mood, terms in MOOD_TERMS.items():
        if any(term.casefold() in text for term in terms):
            return mood
    return None


def immediate_safety_response(message: str) -> str | None:
    text = re.sub(r"\s+", " ", message.casefold())
    if not any(term.casefold() in text for term in RISK_TERMS):
        return None
    return (
        "I'm really sorry you're facing this. You deserve support from a person right now. "
        "If you may act on these thoughts or are in immediate danger, contact your local emergency service now; "
        "otherwise, please reach out to someone you trust and tell them plainly that you need help staying safe. "
        "I’m an AI and can’t provide emergency monitoring."
    )


def fallback_companion_reply(message: str, language: str = "English") -> str:
    mood = detect_mood(message)
    if language == "Hindi":
        return "यह सुनकर अफ़सोस हुआ। अगर आप चाहें, तो बताइए कि अभी सबसे ज़्यादा क्या भारी लग रहा है।" if mood in {"sad", "lonely", "anxious", "stressed"} else "मैं आपकी बात सुन रहा/रही हूँ। आज आप कैसा महसूस कर रहे हैं?"
    if language == "Telugu":
        return "మీకు ఇలా అనిపిస్తున్నందుకు బాధగా ఉంది. మీకు ఇష్టమైతే, ఇప్పుడు ఎక్కువగా ఏ విషయం భారంగా అనిపిస్తోందో చెప్పండి." if mood in {"sad", "lonely", "anxious", "stressed"} else "నేను వినడానికి ఇక్కడ ఉన్నాను. ఈ రోజు మీకు ఎలా అనిపిస్తోంది?"
    if mood == "happy":
        return "That sounds like a good moment. What’s been the best part of your day?"
    if mood in {"sad", "lonely", "anxious", "stressed"}:
        return "I’m sorry things feel heavy right now. Would you like to share what’s weighing on you most?"
    return "I’m here to listen. What would feel helpful to talk through right now?"


def masked_phone(phone: str) -> tuple[bool, str | None]:
    clean = phone.strip()
    return (bool(clean), f"••••{clean[-4:]}" if clean else None)


def public_preferences(row: Any) -> dict[str, Any]:
    values = dict(row)
    present, suffix = masked_phone(values.pop("destination_phone", ""))
    values["destination_phone_present"] = present
    values["destination_phone_masked"] = suffix
    for key in ("mixed_language", "calling_consent", "automatic_mood_calls", "phone_storage_consent", "phone_verified", "reminder_calls_enabled", "store_call_transcripts"):
        values[key] = bool(values[key])
    return values
