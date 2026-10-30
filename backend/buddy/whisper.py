"""Speech-to-text: Whisper on the user's own server (faster-whisper), and for Talk mode,
Groq's hosted Whisper large-v3-turbo when BUDDY_GROQ_KEY is set: about 0.3-0.6 s instead of
1.5-3 s on a small server, and far better with Malayalam. Only what the user says to Buddy in
Talk mode goes to Groq; Listen-in (other people's speech) always stays on this server.
BUDDY_WHISPER_TALK=local keeps everything here. If Groq is down, slow, over its free limit or
hears a language outside "My languages", the local model does it instead.

The phone records short utterances and sends the raw audio (16 kHz, mono,
16-bit); this turns them into text and detects the language, so nothing is sent
to Google's recognizer. Optional: install it with deploy/whisper.sh. The model
size is BUDDY_WHISPER_MODEL ("base" suits a 2 GB server; "small" is more
accurate but needs about 4 GB and is slower).
"""

import asyncio
import io
import time
import wave
from types import SimpleNamespace
import logging
import os
import re
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
        _model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 1)
    return _model


LANGUAGE_CODES = {"english": "en", "hindi": "hi", "malayalam": "ml", "marathi": "mr", "tamil": "ta",
                  "telugu": "te", "kannada": "kn", "bengali": "bn", "gujarati": "gu", "spanish": "es",
                  "portuguese": "pt", "french": "fr", "german": "de", "italian": "it"}


def known_languages() -> list[str]:
    """The user's languages (BUDDY_KNOWN_LANGUAGES names) as Whisper codes."""
    from .config import settings

    names = [n.strip().lower() for n in (settings.known_languages or "").split(",")]
    return [LANGUAGE_CODES[n] for n in names if n in LANGUAGE_CODES] or ["en"]


def _pick_language(info, allowed: list[str] | None, sure: float = 0.6) -> str | None:
    """None to keep Whisper's guess, or the language to transcribe in instead. With allowed languages
    ("auto:ml-IN,en-IN"), only those; otherwise an unsure guess falls back to the user's languages.
    (Noise and Malayalam came out as Italian; a confident guess of another language stays, for Listen-in.)"""
    pool = allowed or (known_languages() if (info.language_probability or 0) < sure else None)
    if not pool or info.language in pool:
        return None
    probs = dict(getattr(info, "all_language_probs", None) or [])
    return max(pool, key=lambda code: probs.get(code, 0.0))


def _echoes_prompt(text: str, prompt: str) -> bool:
    """Whisper reading its own prompt back on a near-silent clip ("Names, Bro's, Gmail, Kochi, India.")."""
    if not prompt:
        return False
    def words(t):
        return [w.removesuffix("'s").removesuffix("’s") for w in re.findall(r"[\w'’]+", t.lower())]
    said, hint = words(text), set(words(prompt))
    if not said:
        return False
    return said[0] == "names" or (len(said) >= 3 and sum(w in hint for w in said) >= len(said) * 0.8)


def _wrong_script(text: str, language: str | None) -> bool:
    """English expected, but the text is mostly other letters ("Enný, sannig að eftir að sína"):
    speech in another language forced through English. Better dropped than answered."""
    if language != "en":
        return False
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 6 and sum(not c.isascii() for c in letters) > len(letters) * 0.08


def _clean(segments, prompt: str = "") -> str:
    """The text, without Whisper's made-up parts: segments it thinks are silence, garbled
    repetitive ones, runs of one word ("Bro, Bro, Bro…") and its own prompt read back.
    '' when nothing real is left."""
    kept = [s for s in segments
            if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0) and s.compression_ratio <= 2.4
            and s.avg_logprob > -1.5]
    text = " ".join(s.text.strip() for s in kept).strip()
    words = [w.strip(".,!?;:\"'").lower() for w in text.split()]
    words = [w for w in words if w]
    if len(words) >= 5 and max(words.count(w) for w in set(words)) > len(words) * 0.5:
        return ""  # one word over and over: a hallucination, not speech
    return "" if _echoes_prompt(text, prompt) else text


def _transcribe(pcm: bytes, language: str | None, prompt: str = "", stats: dict | None = None,
                allowed: list[str] | None = None) -> tuple[str, str]:
    import numpy as np

    audio = np.frombuffer(pcm[: SAMPLE_RATE * 2 * MAX_SECONDS], dtype=np.int16).astype(np.float32) / 32768.0
    with _lock:  # one at a time: a small server has one or two cores
        model = _load()

        def run(lang):
            # temperature 0 only: unsure audio (noise, far-off talk) used to be retried at up to six
            # temperatures, 15+ seconds for nothing; one pass, and made-up text is dropped (_clean).
            return model.transcribe(audio, language=lang, beam_size=1, vad_filter=True,
                                    temperature=0.0, condition_on_previous_text=False,
                                    initial_prompt=prompt or None,  # your names and words
                                    word_timestamps=stats is not None)

        segments, info = run(language)
        if language is None and (better := _pick_language(info, allowed)):
            segments, info = run(better)  # segments are lazy: the first guess was never decoded
        segments = list(segments)
        text = _clean(segments, prompt)
        if _wrong_script(text, language):
            text = ""
        if stats is not None:
            # How sure Whisper was of each word, and how fast: for the English tutor (no audio is kept).
            words = [w for s in segments for w in (getattr(s, "words", None) or [])]
            if words:
                spoken = max(0.1, words[-1].end - words[0].start)
                stats.update(words=len(words), seconds=round(spoken, 2),
                             clarity=round(sum(w.probability for w in words) / len(words), 3),
                             unclear=[w.word.strip(" .,!?") for w in words if w.probability < 0.55 and
                                      len(w.word.strip(" .,!?")) > 2][:10])
    return text, (info.language or "") if text else ""


GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_USD_PER_HOUR = 0.04  # whisper-large-v3-turbo; each request is billed as at least 10 seconds
_groq_resting_until = 0.0
_groq_rest_reason = ""


class GroqSkipped(Exception):
    """Groq can't or shouldn't do this clip: the local model does."""


def groq_limit_seconds() -> int:
    """Seconds until Groq's free limit resets, or 0 if it isn't at its limit."""
    if _groq_rest_reason != "limit":
        return 0
    return max(0, int(_groq_resting_until - time.monotonic()))


def groq_cost(audio_seconds: float) -> float:
    """$0 on Groq's free tier; with BUDDY_GROQ_PAID=1, the paid rate (at least 10 s a request)."""
    if os.getenv("BUDDY_GROQ_PAID", "") != "1":
        return 0.0
    return max(10.0, audio_seconds) / 3600 * GROQ_USD_PER_HOUR


def groq_model() -> str:
    return os.getenv("BUDDY_GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")


def groq_on() -> bool:
    return (os.getenv("BUDDY_WHISPER_TALK", "groq").strip().lower() != "local"
            and bool((os.getenv("BUDDY_GROQ_KEY") or "").strip()))


def _wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm[: SAMPLE_RATE * 2 * MAX_SECONDS])
    return out.getvalue()


async def _groq(pcm: bytes, hint: str | None, prompt: str, allowed: list[str] | None, http=None) -> tuple[str, str]:
    global _groq_resting_until, _groq_rest_reason
    import httpx

    if time.monotonic() < _groq_resting_until:
        raise GroqSkipped(f"resting after {'its free limit' if _groq_rest_reason == 'limit' else 'an error'}")
    data = {"model": groq_model(), "response_format": "verbose_json", "temperature": "0"}
    if hint:
        data["language"] = hint
    if prompt:
        data["prompt"] = prompt[:600]  # Whisper reads at most ~224 tokens of prompt
    headers = {"Authorization": f"Bearer {os.environ['BUDDY_GROQ_KEY'].strip()}"}
    try:
        client = http or httpx.AsyncClient(timeout=8)
        try:
            response = await client.post(GROQ_URL, data=data, headers=headers,
                                         files={"file": ("speech.wav", _wav(pcm), "audio/wav")})
        finally:
            if http is None:
                await client.aclose()
    except Exception as e:  # network, timeout
        _groq_resting_until, _groq_rest_reason = time.monotonic() + 30, "error"
        raise GroqSkipped(type(e).__name__) from e
    if response.status_code == 429:  # the free limit: rest a while, the local model carries on
        _groq_resting_until = time.monotonic() + float(response.headers.get("retry-after") or 60)
        _groq_rest_reason = "limit"
        raise GroqSkipped("at its free limit")
    if response.status_code >= 400:
        _groq_resting_until = time.monotonic() + (600 if response.status_code in (401, 403) else 30)
        _groq_rest_reason = "error"
        raise GroqSkipped(f"HTTP {response.status_code}")
    body = response.json()
    name = str(body.get("language") or "").lower()
    lang = LANGUAGE_CODES.get(name, name[:2])
    if allowed and not hint and lang and lang not in allowed:
        raise GroqSkipped(f"heard {name}, outside My languages")  # the local model picks among them
    segments = [SimpleNamespace(text=sg.get("text", ""), no_speech_prob=sg.get("no_speech_prob", 0.0),
                                avg_logprob=sg.get("avg_logprob", 0.0), compression_ratio=sg.get("compression_ratio", 1.0))
                for sg in body.get("segments") or []]
    text = _clean(segments, prompt) if segments else _clean([SimpleNamespace(
        text=body.get("text", ""), no_speech_prob=0.0, avg_logprob=0.0, compression_ratio=1.0)], prompt)
    if _wrong_script(text, hint):
        text = ""
    return text, (lang if text else "")


async def transcribe(pcm: bytes, language: str = "", prompt: str = "", stats: dict | None = None,
                     mode: str = "", meta: dict | None = None) -> tuple[str, str]:
    """(text, detected language code). language: a hint like "es" or "es-419", or "" to detect.
    prompt: names and words to spell right (vocabulary.whisper_prompt). mode "talk" may use Groq;
    meta gets which engine did it ("groq" or "local") and why Groq didn't, if it didn't."""
    meta = meta if meta is not None else {}
    # "auto" or "auto:ml-IN,en-IN" (switching between languages): let Whisper detect each time.
    hint = language.split("-")[0].lower() if language and not language.startswith("auto") else None
    if language.startswith("auto:"):
        names = {"ml": "Malayalam", "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "kn": "Kannada",
                 "es": "Spanish", "pt": "Portuguese"}
        mix = [names.get(t.split("-")[0], t) for t in language[5:].split(",") if t]
        prompt = (prompt + " " if prompt else "") + f"Speaking {' and '.join(mix)}, often mixed in one sentence."
    allowed = [t.split("-")[0].lower() for t in language[5:].split(",") if t] if language.startswith("auto:") else None
    if mode == "talk" and groq_on():  # never Listen-in: other people's voices stay on this server
        started = time.monotonic()
        try:
            text, lang = await _groq(pcm, hint, prompt, allowed)
            meta.update(engine="groq", seconds=time.monotonic() - started,
                        audio_seconds=len(pcm) / (SAMPLE_RATE * 2))
            return text, lang
        except GroqSkipped as e:
            meta["groq_skipped"] = str(e)
            log.info("Groq Whisper skipped (%s): local Whisper instead", e)
    meta["engine"] = "local"
    return await asyncio.to_thread(_transcribe, pcm, hint, prompt, stats, allowed)
