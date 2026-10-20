"""Voice message transcription using faster-whisper (open-source, runs locally)."""

import asyncio
import io
import logging
import threading

log = logging.getLogger(__name__)

# Keep one resident model and serialize its complete use, including lazy segment
# iteration. A cancelled asyncio caller cannot stop an already running thread.
_cached_model: tuple[str, object] | None = None
_model_lock = threading.RLock()
_admission = asyncio.Semaphore(1)
_workers: set[asyncio.Task[str]] = set()
_closed = False

VALID_MODELS = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_MODEL = "small"
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_SECONDS = 600
_SAMPLE_RATE = 16000


class AudioTooLongError(ValueError):
    """The decoded audio exceeds the transcription duration budget."""


def _get_model(model_size: str = DEFAULT_MODEL):
    """Load one Whisper model lazily (CPU, int8), replacing the previous size."""
    global _cached_model

    if model_size not in VALID_MODELS:
        model_size = DEFAULT_MODEL

    with _model_lock:
        if _cached_model is None or _cached_model[0] != model_size:
            from faster_whisper import WhisperModel

            # Drop the old model before allocating its replacement, rather than
            # retaining every model size selected by different users.
            _cached_model = None
            log.info("Loading Whisper model '%s' ...", model_size)
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _cached_model = (model_size, model)
            log.info("Whisper model loaded: %s", model_size)
        return _cached_model[1]


async def transcribe_voice(
    file_bytes: bytes,
    language: str | None = None,
    model_size: str = DEFAULT_MODEL,
) -> str:
    """Transcribe audio bytes supported by faster-whisper to text.

    Args:
        file_bytes: Raw audio bytes from Telegram or the web dashboard.
        language: ISO language code (e.g. "en", "es") or None for auto-detect.
        model_size: Whisper model size to use.

    Returns:
        Transcribed text string.
    """
    if _closed:
        raise RuntimeError("Audio transcription is shutting down")
    if not file_bytes:
        raise ValueError("Audio is empty")
    if len(file_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("Audio must be no larger than 20 MiB")

    # Queue on the event loop, not in executor threads. The worker owns this
    # slot until it finishes, even if the request that submitted it is cancelled.
    admission = _admission
    await admission.acquire()
    if _closed:
        admission.release()
        raise RuntimeError("Audio transcription is shutting down")
    worker = asyncio.create_task(
        asyncio.to_thread(_transcribe_sync, file_bytes, language, model_size)
    )
    _workers.add(worker)

    def finished(task: asyncio.Task[str]) -> None:
        _workers.discard(task)
        admission.release()
        if not task.cancelled():
            # Observe failures after caller cancellation; awaiting callers still
            # receive the original exception through shield().
            task.exception()

    worker.add_done_callback(finished)
    return await asyncio.shield(worker)


async def close_transcriber() -> None:
    """Stop admission and asynchronously drain the actual native worker.

    Python cannot interrupt inference already running in a thread. Joining it
    keeps resource ownership explicit and lets its stream/model finish safely.
    """
    global _closed
    _closed = True
    if _workers:
        await asyncio.gather(
            *(asyncio.shield(worker) for worker in tuple(_workers)),
            return_exceptions=True,
        )
    if _cached_model is not None:
        await asyncio.to_thread(_release_model)


def _release_model() -> None:
    global _cached_model
    with _model_lock:
        _cached_model = None


def _decode_audio(file_bytes: bytes):
    """Decode a bounded mono waveform without trusting container duration.

    faster-whisper's convenience decoder accumulates the whole recording before
    returning. Count resampled frames instead, so a tiny compressed recording
    cannot allocate arbitrarily large PCM buffers before its duration is checked.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=_SAMPLE_RATE)
    chunks = []
    sample_count = 0

    def collect(frame):
        nonlocal sample_count
        sample_count += frame.samples
        if sample_count > MAX_AUDIO_SECONDS * _SAMPLE_RATE:
            raise AudioTooLongError("Audio must be 10 minutes or shorter")
        chunks.append(frame.to_ndarray().reshape(-1))

    with io.BytesIO(file_bytes) as source:
        # The primary input uses Python I/O, so it needs no libav protocol.
        # Disable all secondary opens: an autodetected playlist must not read
        # another local file or fetch a URL embedded in the uploaded bytes.
        with av.open(
            source,
            mode="r",
            metadata_errors="ignore",
            options={"protocol_whitelist": ""},
        ) as container:
            for frame in container.decode(audio=0):
                # Discontinuous timestamps should not change sample accounting.
                frame.pts = None
                for converted in resampler.resample(frame):
                    collect(converted)
            for converted in resampler.resample(None):
                collect(converted)
    if not sample_count:
        raise ValueError("Audio contains no samples")
    audio = np.concatenate(chunks).astype(np.float32)
    audio /= 32768.0
    return audio


def _transcribe_sync(
    file_bytes: bytes,
    language: str | None = None,
    model_size: str = DEFAULT_MODEL,
) -> str:
    """Own bounded decoding and the model for the complete blocking operation."""
    if model_size not in VALID_MODELS:
        model_size = DEFAULT_MODEL
    with _model_lock:
        audio = _decode_audio(file_bytes)
        model = _get_model(model_size)
        kwargs: dict = {"beam_size": 5}
        if language:
            kwargs["language"] = language
        segments, info = model.transcribe(audio, **kwargs)
        text = " ".join(seg.text.strip() for seg in segments)
        log.info(
            "Transcribed %.1fs audio → %d chars (lang=%s, model=%s)",
            info.duration,
            len(text),
            info.language,
            model_size,
        )
        return text
