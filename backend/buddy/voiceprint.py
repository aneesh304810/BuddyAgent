"""Voice match: in Talk mode, only the user's own voice counts.

The user reads five short sentences once (Settings -> Teach Buddy my voice). Each is turned into a
voiceprint: 256 numbers that describe how a voice sounds (a speaker-embedding model, about 25 MB,
on this server; no audio is kept). Every Talk clip is compared with it before transcription:

  - sounds like the user          -> transcribed and answered as usual
  - doesn't, or Buddy isn't sure  -> ignored, never transcribed or sent to Groq (stay quiet when unsure)
  - too short to judge (< 1 s)    -> accepted only right after Buddy spoke to the user ("yes", "thanks")

Clips that match well nudge the voiceprint, so it follows how the user sounds over time; "Buddy, that
was me" adds the last ignored clip. Listen-in is unaffected. The voiceprint is biometric data: it stays
in Buddy's own (encrypted) database and "forget my voice" deletes it. Install: deploy/voice.sh.
"""

import json
import logging
import math
import os
import threading
import time
from pathlib import Path

from .config import settings
from .memory import Memory

log = logging.getLogger(__name__)

KEY = "voiceprint"
ON_KEY = "voiceprint:on"
SAMPLE_RATE = 16_000
MODEL_FILE = "wespeaker_en_voxceleb_resnet34.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/" + MODEL_FILE)
# Cosine similarity: you at or above this. Bluetooth headset audio flattens voices, so the user's own
# clips scored 0.39-0.50 in testing; other people and the TV usually score well below 0.3.
THRESHOLD = float(os.getenv("BUDDY_VOICE_THRESHOLD", "0.4"))
MIN_SECONDS = 1.0  # shorter clips can't be judged reliably
FOLLOW_UP_SECONDS = 20  # after Buddy spoke, a short "yes" or "thanks" is let through
ENROLL_SENTENCES = [
    "Hey Buddy, what's the weather like in Kochi this weekend?",
    "Remind me to call my mother tomorrow evening at seven.",
    "Find me the cheapest flight from Miami to Lima next month.",
    "Read me the latest news, and keep it short please.",
    "Thanks Buddy, that's all for now. Talk to you later.",
]
_extractor = None
_lock = threading.Lock()
_last_ignored: list[float] | None = None  # for "that was me"


class VoiceMatchUnavailable(Exception):
    pass


def model_path() -> Path:
    default = (Path(settings.db_path).parent if str(settings.db_path) != ":memory:" else Path(".")) / "models"
    return Path(os.getenv("BUDDY_VOICE_MODEL", str(default / MODEL_FILE)))


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return model_path().exists()


def _load():
    global _extractor
    if _extractor is None:
        try:
            import sherpa_onnx
        except ImportError as e:
            raise VoiceMatchUnavailable("Voice match isn't installed yet: run sudo bash /opt/buddy/deploy/voice.sh") from e
        if not model_path().exists():
            raise VoiceMatchUnavailable("The voice model is missing: run sudo bash /opt/buddy/deploy/voice.sh")
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path()), num_threads=1,
                                                             debug=False, provider="cpu")
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return _extractor


def embed(pcm: bytes) -> list[float]:
    """The voiceprint of one clip (16 kHz mono 16-bit), normalised to length 1."""
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    with _lock:
        extractor = _load()
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=samples)
        stream.input_finished()
        vector = extractor.compute(stream)
    return _unit(list(vector))


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def stored(memory: Memory) -> dict | None:
    try:
        return json.loads(memory.get(KEY) or "null")
    except json.JSONDecodeError:
        return None


def on(memory: Memory) -> bool:
    return memory.get(ON_KEY) != "0" and stored(memory) is not None


def _save(memory: Memory, vector: list[float], samples: int) -> None:
    memory.set(KEY, json.dumps({"vector": [round(x, 6) for x in _unit(vector)], "samples": samples,
                                "updated": time.strftime("%Y-%m-%d")}))


def enroll(memory: Memory, clips: list[bytes], embedder=embed) -> dict:
    """The voiceprint from the enrolment sentences. Clips that don't sound like the others (a cough,
    someone else) are left out."""
    vectors = [embedder(c) for c in clips if len(c) >= SAMPLE_RATE * 2 * MIN_SECONDS]
    if len(vectors) < 3:
        return {"ok": False, "why": "Too little speech: read each sentence aloud, close to the phone."}
    mean = _unit([sum(col) / len(vectors) for col in zip(*vectors)])
    good = [v for v in vectors if similarity(v, mean) >= 0.6] or vectors
    mean = _unit([sum(col) / len(good) for col in zip(*good)])
    _save(memory, mean, len(good))
    memory.set(ON_KEY, "1")
    consistency = sum(similarity(v, mean) for v in good) / len(good)
    return {"ok": True, "used": len(good), "of": len(vectors), "consistency": round(consistency, 2)}


def check(memory: Memory, pcm: bytes, since_buddy_spoke: float, embedder=embed) -> tuple[str, float | None]:
    """('you' | 'not_you' | 'short', score) for a Talk clip. 'short': too short to judge, so accepted
    only within the follow-up window. Matches nudge the voiceprint towards how the user sounds now."""
    global _last_ignored
    print_ = stored(memory)
    if not print_:
        return "you", None
    if len(pcm) < SAMPLE_RATE * 2 * MIN_SECONDS:
        return ("you" if since_buddy_spoke <= FOLLOW_UP_SECONDS else "short"), None
    vector = embedder(pcm)
    score = similarity(vector, print_["vector"])
    if score >= THRESHOLD:
        if score >= THRESHOLD + 0.15 and print_["samples"] < 200:  # clearly you: learn a little
            n = print_["samples"]
            _save(memory, [(a * n + b) / (n + 1) for a, b in zip(print_["vector"], vector)], n + 1)
        return "you", score
    _last_ignored = vector
    return "not_you", score


def that_was_me(memory: Memory) -> str:
    """The last ignored clip was the user: add it to the voiceprint."""
    global _last_ignored
    print_ = stored(memory)
    if not print_ or _last_ignored is None:
        return "There's no recently ignored clip to add."
    n = print_["samples"]
    _save(memory, [(a * n + 2 * b) / (n + 2) for a, b in zip(print_["vector"], _last_ignored)], n + 2)
    _last_ignored = None
    return "Got it: I've added that to your voiceprint, so I'll recognise you better."


VOICE_TOOL = {
    "name": "voice_match",
    "description": "Voice match for Talk mode: Buddy only answers the user's own voice there. status; turn_off / "
    "turn_on; forget (delete the voiceprint); that_was_me (the user says you ignored them: learn from the last "
    "ignored clip). To set it up, tell them: Settings -> Teach Buddy my voice.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "turn_on", "turn_off", "forget", "that_was_me"]}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "forget":
        memory.set(KEY, "null")
        memory.set(ON_KEY, "0")
        return "Deleted the voiceprint: Talk mode answers any voice again until they teach Buddy their voice."
    if action == "turn_off":
        memory.set(ON_KEY, "0")
        return "Voice match is off: Talk mode answers any voice."
    if action == "turn_on":
        if not stored(memory):
            return "There's no voiceprint yet: Settings -> Teach Buddy my voice."
        memory.set(ON_KEY, "1")
        return "Voice match is on: in Talk mode only the user's voice is answered."
    if action == "that_was_me":
        return that_was_me(memory)
    print_ = stored(memory)
    return json.dumps({"installed": available(), "voiceprint": bool(print_), "on": on(memory),
                       "learned_from_clips": print_["samples"] if print_ else 0,
                       "updated": print_["updated"] if print_ else None})
