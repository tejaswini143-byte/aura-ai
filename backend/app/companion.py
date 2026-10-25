from __future__ import annotations

import re
from typing import Any

MOOD_TERMS = {
    "sad": ("sad", "down", "unhappy", "low", "feeling bad", "baadha ga undi", "badha ga undi", "baadha ga undhi", "బాధగా ఉంది", "బాధగా", "దుఃఖంగా", "उदास", "दुखी"),
    "lonely": ("lonely", "alone", "isolated", "ontariga", "ontariga unna", "ఒంటరిగా", "अकेला", "अकेली", "अकेलापन"),
    "anxious": ("anxious", "anxiety", "worried", "nervous", "tension ga undi", "tension ga unna", "ఆందోళన", "చింతగా", "చింత", "चिंता", "परेशान", "घबराहट"),
    "stressed": ("stressed", "overwhelmed", "pressure", "stress ga undi", "చాలా ఒత్తిడిగా", "ఒత్తిడి", "तनाव", "दबाव"),
    "happy": ("happy", "great", "good today", "happy ga unna", "chala happy", "సంతోషంగా", "సంతోషంగా ఉంది", "సంతోషంగా ఉంది", "ఈ రోజు చాలా సంతోషంగా ఉంది", "ఖుషీగా", "खुश", "खुशी", "बहुत अच्छा"),
}
RISK_TERMS = (
    "kill myself", "end my life", "suicide", "self harm", "hurt myself",
    "आत्महत्या", "खुद को नुकसान", "నా ప్రాణం తీసుకోవాలి", "ఆత్మహత్య",
)


def detect_mood(message: str) -> str | None:
    text = re.sub(r"[^\w\s]", " ", message.casefold())
    text = re.sub(r"\s+", " ", text).strip()
    for mood, terms in MOOD_TERMS.items():
        for term in terms:
            normalized = re.sub(r"\s+", " ", term.casefold()).strip()
            if re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text):
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


def detect_message_language(message: str, preferred_language: str = "English") -> str:
    """Best-effort language hint for short English/Hindi/Telugu companion messages."""
    if re.search(r"[ఀ-౿]", message):
        return "Telugu"
    if re.search(r"[ऀ-ॿ]", message):
        return "Hindi"

    text = re.sub(r"[^a-z0-9\s]", " ", message.casefold())
    text = re.sub(r"\s+", " ", text).strip()
    telugu_cues = (
        "naku", "naaku", "baadha", "badha", "ontariga", "undhi", "undi",
        "chala", "cheppu", "em jarigindi", "tension ga", "happy ga", "ledu",
        "enduku", "ela unnav", "ela unnava", "kavali", "avuthundi", "avutundi",
    )
    hindi_cues = (
        "mujhe", "main", "nahi", "nahin", "kyun", "kya", "hoon", "hai",
        "bahut", "akela", "akeli", "mera", "meri", "kaise", "aap", "tum",
        "pareshan", "lag raha", "lag rahi", "theek nahi",
    )
    if any(re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", text) for cue in telugu_cues):
        return "Telugu"
    if any(re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", text) for cue in hindi_cues):
        return "Hindi"
    english_cues = (
        "i", "i am", "im", "i feel", "feeling", "today", "because", "please",
        "what", "why", "how", "can you", "i need", "i want", "i'm", "my",
        "don't", "do not", "i was", "this is",
    )
    if any(re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", text) for cue in english_cues):
        return "English"
    return preferred_language if preferred_language in {"English", "Hindi", "Telugu"} else "English"


def fallback_companion_reply(message: str, language: str = "English") -> str:
    mood = detect_mood(message)
    detected_language = detect_message_language(message, language)
    normalized = re.sub(r"[^a-z0-9\s]", " ", message.casefold())
    romanized_telugu = not re.search(r"[ఀ-౿]", message) and any(
        re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", normalized)
        for cue in ("naku", "naaku", "baadha", "badha", "ontariga", "undhi", "chala", "cheppu", "ledu")
    )
    romanized_hindi = not re.search(r"[ऀ-ॿ]", message) and any(
        re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", normalized)
        for cue in ("mujhe", "nahi", "nahin", "kyun", "bahut", "akela", "akeli", "kaise")
    )
    if detected_language == "Telugu" and romanized_telugu:
        if mood in {"sad", "lonely", "anxious", "stressed"}:
            return "Ayyo, ala anipistundani vintunte baadhaga undi. Em jarigindo mellaga cheppu; nenu vintanu."
        if mood == "happy":
            return "Abbaa, chaala baagundi! Ee roju lo neeku nachina best moment enti?"
        return "Nenu vintunnanu. Ee roju ela feel avutunnavu?"
    if detected_language == "Hindi" and romanized_hindi:
        if mood in {"sad", "lonely", "anxious", "stressed"}:
            return "Yeh sun kar afsos hua. Agar tum chaho, batao abhi sabse zyada kya pareshaan kar raha hai."
        if mood == "happy":
            return "Wah, yeh toh achhi baat hai! Aaj ke din ka sabse achha hissa kya tha?"
        return "Main sun raha/rahi hoon. Aaj tum kaisa mehsoos kar rahe ho?"
    if detected_language == "Hindi":
        return "यह सुनकर अफ़सोस हुआ। अगर आप चाहें, तो बताइए कि अभी सबसे ज़्यादा क्या भारी लग रहा है।" if mood in {"sad", "lonely", "anxious", "stressed"} else "मैं आपकी बात सुन रहा/रही हूँ। आज आप कैसा महसूस कर रहे हैं?"
    if detected_language == "Telugu":
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
