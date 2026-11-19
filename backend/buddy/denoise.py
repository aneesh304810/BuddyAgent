"""Noise removal for Talk mode's live hearing: on this server, and only when you switch it on.

A small neural model (GTCRN, half a megabyte, MIT licence) run through sherpa-onnx, the library voice match already
uses. It takes your speech as it arrives and takes out the background (a fan, traffic, a restaurant's clatter), a
few hundredths of a second behind. Nothing leaves the server for this.

It is off unless you ask for it, for a reason. Deepgram, whose model does the hearing, advises against cleaning
audio before recognition: the cleaning takes parts of the speech with the noise, and its model was trained on noisy
audio, so recognition usually gets worse, not better. So Buddy sends your speech unaltered by default.

  BUDDY_NOISE_CANCEL   off (default) | talk: Talk mode's live turns are cleaned before they are heard

Voice match always gets the unaltered audio (it was taught on your unaltered voice), and a turn that falls back to
being heard as a clip is heard unaltered too. Phone calls are not covered: a phone line is 8 kHz and already
processed by the phone network. Install the model with: sudo bash /opt/buddy/deploy/noise.sh
"""

import array
import logging
import os
from pathlib import Path

from . import gateway
from .config import settings

log = logging.getLogger("buddy.denoise")

RATE = 16_000
MODEL_FILE = "gtcrn_simple.onnx"
MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/" + MODEL_FILE


def setting(env=None) -> str:
    """"talk" or "off"."""
    raw = (gateway._secret(env, "BUDDY_NOISE_CANCEL") or "off").strip().lower()
    return "talk" if raw in ("talk", "on", "1", "yes", "true") else "off"


def model_path() -> Path:
    default = (Path(settings.db_path).parent if str(settings.db_path) != ":memory:" else Path(".")) / "models"
    return Path(os.getenv("BUDDY_NOISE_MODEL", str(default / MODEL_FILE)))


def available() -> bool:
    """The model is on this server and the library can run it."""
    if not model_path().is_file():
        return False
    try:
        import sherpa_onnx  # noqa: PLC0415
    except ImportError:
        return False
    return hasattr(sherpa_onnx, "OnlineSpeechDenoiser")


def wanted(env=None) -> bool:
    """Switched on, and able to run. Switched on without the model: said once in the log, and nothing is cleaned."""
    if setting(env) != "talk":
        return False
    if not available():
        log.warning("noise removal is switched on but its model isn't installed: sudo bash /opt/buddy/deploy/noise.sh")
        return False
    return True


class Cleaner:
    """One stream's noise removal: 16 kHz, 16-bit, mono in; the same out, as much as is ready."""

    def __init__(self, engine=None):
        if engine is None:
            import sherpa_onnx  # noqa: PLC0415
            config = sherpa_onnx.OnlineSpeechDenoiserConfig(model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(model_path())),
                num_threads=1, provider="cpu"))
            if not config.validate():
                raise RuntimeError("the noise removal model couldn't be loaded")
            engine = sherpa_onnx.OnlineSpeechDenoiser(config)
        self.engine = engine

    @staticmethod
    def _floats(pcm: bytes) -> list[float]:
        samples = array.array("h")
        samples.frombytes(pcm[:len(pcm) // 2 * 2])
        return [s / 32768.0 for s in samples]

    @staticmethod
    def _pcm(samples) -> bytes:
        return array.array("h", (max(-32768, min(32767, int(round(s * 32768.0)))) for s in samples)).tobytes()

    def clean(self, pcm: bytes) -> bytes:
        """The cleaned audio that is ready (a little less than went in at first: the model works a frame behind)."""
        if not pcm:
            return b""
        return self._pcm(self.engine.run(self._floats(pcm), RATE).samples)

    def rest(self) -> bytes:
        """What the model was still holding, at the end of a stream."""
        return self._pcm(self.engine.flush().samples)

    def reset(self) -> None:
        """A new turn: nothing carried over from the last one."""
        self.engine.reset()
