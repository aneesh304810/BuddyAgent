"""Speech-to-text on the user's own server, with Whisper (faster-whisper).

The phone records short utterances and sends the raw audio (16 kHz, mono,
16-bit); this turns them into text and detects the language, so nothing is sent
to Google's recognizer. Optional: install it with deploy/whisper.sh. The model
size is BUDDY_WHISPER_MODEL ("base" suits a 2 GB server; "small" is more
accurate but needs about 4 GB and is slower).
"""

import asyncio
import logging
import os
import threading

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
MAX_SECONDS = 30
_model = None
_lock = threading.Lock()


class WhisperUnavailable(Exception):
    pass


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise WhisperUnavailable(
                "Whisper isn't installed on the server yet: run sudo bash /opt/buddy/deploy/whisper.sh") from e
        size = os.getenv("BUDDY_WHISPER_MODEL", "base")
        log.info("loading Whisper %s", size)
        _model = WhisperModel(size, device="cpu", compute_type="int8")
    return _model


def _transcribe(pcm: bytes, language: str | None, prompt: str = "", stats: dict | None = None) -> tuple[str, str]:
    import numpy as np

    audio = np.frombuffer(pcm[: SAMPLE_RATE * 2 * MAX_SECONDS], dtype=np.int16).astype(np.float32) / 32768.0
    with _lock:  # one at a time: a small server has one or two cores
        model = _load()
        segments, info = model.transcribe(audio, language=language, beam_size=1, vad_filter=True,
                                          condition_on_previous_text=False,
                                          initial_prompt=prompt or None,  # your names and words
                                          word_timestamps=stats is not None)
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        if stats is not None:
            # How sure Whisper was of each word, and how fast: for the English tutor (no audio is kept).
            words = [w for s in segments for w in (getattr(s, "words", None) or [])]
            if words:
                spoken = max(0.1, words[-1].end - words[0].start)
                stats.update(words=len(words), seconds=round(spoken, 2),
                             clarity=round(sum(w.probability for w in words) / len(words), 3),
                             unclear=[w.word.strip(" .,!?") for w in words if w.probability < 0.55 and
                                      len(w.word.strip(" .,!?")) > 2][:10])
    return text, info.language or ""


async def transcribe(pcm: bytes, language: str = "", prompt: str = "", stats: dict | None = None) -> tuple[str, str]:
    """(text, detected language code). language: a hint like "es" or "es-419", or "" to detect.
    prompt: names and words to spell right (vocabulary.whisper_prompt)."""
    # "auto" or "auto:ml-IN,en-IN" (switching between languages): let Whisper detect each time.
    hint = language.split("-")[0].lower() if language and not language.startswith("auto") else None
    if language.startswith("auto:"):
        names = {"ml": "Malayalam", "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "kn": "Kannada",
                 "es": "Spanish", "pt": "Portuguese"}
        mix = [names.get(t.split("-")[0], t) for t in language[5:].split(",") if t]
        prompt = (prompt + " " if prompt else "") + f"Speaking {' and '.join(mix)}, often mixed in one sentence."
    return await asyncio.to_thread(_transcribe, pcm, hint, prompt, stats)
