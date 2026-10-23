"""Local transcription resource ownership and cancellation behavior."""

import asyncio
import io
import threading
import wave
import weakref
from types import SimpleNamespace

import faster_whisper
import numpy as np
import pytest

from utils import transcribe


@pytest.fixture(autouse=True)
def isolated_transcriber(monkeypatch):
    monkeypatch.setattr(transcribe, "_cached_model", None)
    monkeypatch.setattr(transcribe, "_model_lock", threading.RLock())
    monkeypatch.setattr(transcribe, "_admission", asyncio.Semaphore(1))
    monkeypatch.setattr(transcribe, "_workers", set())
    monkeypatch.setattr(transcribe, "_closed", False)


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


def result(*texts):
    return (
        (SimpleNamespace(text=text) for text in texts),
        SimpleNamespace(duration=1.0, language="en"),
    )


def wav_bytes(samples=1600, *, sample_rate=16000, channels=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x40" * samples * channels)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_models_are_lazy_reused_and_released_before_replacement(monkeypatch):
    sizes = []
    references = []
    waveforms = []
    options = []

    class Model:
        def __init__(self, size, **kwargs):
            assert kwargs == {"device": "cpu", "compute_type": "int8"}
            assert not references or references[-1]() is None
            sizes.append(size)
            references.append(weakref.ref(self))

        def transcribe(self, audio, **kwargs):
            assert isinstance(audio, np.ndarray)
            assert audio.dtype == np.float32
            np.testing.assert_array_equal(audio, np.full(1600, 0.5))
            waveforms.append(weakref.ref(audio))
            options.append(kwargs)
            return result(" hello ", " world ")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    assert sizes == []
    assert await transcribe.transcribe_voice(wav_bytes(), "zh", "tiny") == "hello world"
    assert (
        await transcribe.transcribe_voice(wav_bytes(), model_size="tiny")
        == "hello world"
    )
    assert sizes == ["tiny"]
    assert (
        await transcribe.transcribe_voice(wav_bytes(), model_size="base")
        == "hello world"
    )
    assert sizes == ["tiny", "base"]
    assert references[0]() is None
    assert references[1]() is not None
    assert options == [
        {"beam_size": 5, "language": "zh"},
        {"beam_size": 5},
        {"beam_size": 5},
    ]
    assert all(reference() is None for reference in waveforms)


@pytest.mark.asyncio
async def test_invalid_model_uses_default_and_empty_audio_does_not_load(monkeypatch):
    sizes = []

    class Model:
        def __init__(self, size, **kwargs):
            sizes.append(size)

        def transcribe(self, audio, **kwargs):
            return result()

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    with pytest.raises(ValueError, match="Audio is empty"):
        await transcribe.transcribe_voice(b"")
    assert sizes == []
    assert await transcribe.transcribe_voice(wav_bytes(), model_size="unknown") == ""
    assert sizes == [transcribe.DEFAULT_MODEL]


@pytest.mark.asyncio
async def test_running_cancellation_keeps_waveform_and_worker_slot_until_completion(
    monkeypatch,
):
    started = threading.Event()
    finish = threading.Event()
    waveforms = []
    submitted = []
    original_to_thread = asyncio.to_thread

    async def record_submission(*args):
        submitted.append(args)
        return await original_to_thread(*args)

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            waveforms.append(weakref.ref(audio))

            def segments():
                if len(waveforms) == 1:
                    started.set()
                    assert finish.wait(3), "test did not release worker"
                assert audio.size in (1600, 3200)
                yield SimpleNamespace(text="first" if audio.size == 1600 else "next")

            return segments(), SimpleNamespace(duration=1.0, language="en")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    monkeypatch.setattr(transcribe.asyncio, "to_thread", record_submission)
    first = asyncio.create_task(transcribe.transcribe_voice(wav_bytes()))
    queued = None
    next_request = None
    try:
        await wait_until(started.is_set)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 0.5)
        assert waveforms[0]() is not None

        queued = asyncio.create_task(transcribe.transcribe_voice(wav_bytes()))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        next_request = asyncio.create_task(transcribe.transcribe_voice(wav_bytes(3200)))
        await asyncio.sleep(0)
        assert len(submitted) == 1
        assert not next_request.done()
        assert len(waveforms) == 1

        finish.set()
        assert await asyncio.wait_for(next_request, 3) == "next"
        assert len(submitted) == 2
        assert all(reference() is None for reference in waveforms)
    finally:
        finish.set()
        await wait_until(lambda: not transcribe._workers)
        for task in (first, queued, next_request):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_failed_worker_releases_slot_without_unobserved_error(
    monkeypatch, cancel_caller
):
    started = threading.Event()
    finish = threading.Event()
    loop_errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            def segments():
                started.set()
                assert finish.wait(3), "test did not release worker"
                raise RuntimeError("decoder failed")
                yield  # Preserve lazy segment iteration.

            return segments(), SimpleNamespace(duration=1.0, language="en")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    request = asyncio.create_task(transcribe.transcribe_voice(wav_bytes()))
    try:
        await wait_until(started.is_set)
        if cancel_caller:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        finish.set()
        if not cancel_caller:
            with pytest.raises(RuntimeError, match="decoder failed"):
                await request
        await wait_until(lambda: not transcribe._workers)
        assert not transcribe._admission.locked()
        assert not loop_errors

        monkeypatch.setattr(Model, "transcribe", lambda *_args, **_kwargs: result("ok"))
        assert await transcribe.transcribe_voice(wav_bytes()) == "ok"
    finally:
        finish.set()
        await wait_until(lambda: not transcribe._workers)
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_load_failure_can_retry_without_retaining_previous_model(monkeypatch):
    calls = 0

    class Model:
        def __init__(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("model unavailable")

        def transcribe(self, audio, **kwargs):
            return result("recovered")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    with pytest.raises(RuntimeError, match="model unavailable"):
        await transcribe.transcribe_voice(wav_bytes())
    assert transcribe._cached_model is None
    assert await transcribe.transcribe_voice(wav_bytes()) == "recovered"


@pytest.mark.parametrize("sample_rate, channels", [(16000, 1), (48000, 2)])
def test_decoder_resamples_real_audio_to_bounded_mono(sample_rate, channels):
    audio = transcribe._decode_audio(
        wav_bytes(sample_rate // 10, sample_rate=sample_rate, channels=channels)
    )
    assert audio.shape == (1600,)
    assert audio.dtype == np.float32
    assert np.all(np.isfinite(audio))


@pytest.mark.asyncio
async def test_actual_duration_limit_precedes_model_load_and_includes_resampler_tail(
    monkeypatch,
):
    monkeypatch.setattr(transcribe, "MAX_AUDIO_SECONDS", 0.1)
    loaded = []
    monkeypatch.setattr(
        faster_whisper, "WhisperModel", lambda *args, **kwargs: loaded.append(args)
    )
    # The exact boundary passes; one extra output sample is refused even though
    # the input is a tiny file and carries no Telegram duration metadata.
    assert transcribe._decode_audio(wav_bytes(1600)).shape == (1600,)
    assert transcribe._decode_audio(wav_bytes(4800, sample_rate=48000)).shape == (1600,)
    for data in (wav_bytes(1601), wav_bytes(4803, sample_rate=48000)):
        with pytest.raises(transcribe.AudioTooLongError, match="10 minutes"):
            await transcribe.transcribe_voice(data)
    assert not loaded
    assert not transcribe._workers


@pytest.mark.asyncio
async def test_invalid_or_oversized_audio_does_not_load_model(monkeypatch):
    loaded = []
    monkeypatch.setattr(
        faster_whisper, "WhisperModel", lambda *args, **kwargs: loaded.append(args)
    )
    with pytest.raises(Exception):
        await transcribe.transcribe_voice(b"not audio")
    with pytest.raises(ValueError, match="no samples"):
        await transcribe.transcribe_voice(wav_bytes(0))
    monkeypatch.setattr(transcribe, "MAX_AUDIO_BYTES", 10)
    with pytest.raises(ValueError, match="20 MiB"):
        await transcribe.transcribe_voice(wav_bytes())
    assert not loaded


def test_decoder_stops_at_first_over_limit_frame_and_closes_input(monkeypatch):
    import av

    decoded = []
    converted = []
    closed = []

    class Frame:
        samples = 3

        def to_ndarray(self):
            converted.append(True)
            return np.zeros((1, self.samples), dtype=np.int16)

    class Container:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def decode(self, **kwargs):
            for _ in range(100):
                decoded.append(True)
                yield Frame()

    monkeypatch.setattr(transcribe, "_SAMPLE_RATE", 4)
    monkeypatch.setattr(transcribe, "MAX_AUDIO_SECONDS", 1)
    monkeypatch.setattr(av, "open", lambda *args, **kwargs: Container())
    monkeypatch.setattr(
        av,
        "AudioResampler",
        lambda **kwargs: SimpleNamespace(resample=lambda frame: [frame]),
    )
    with pytest.raises(transcribe.AudioTooLongError, match="10 minutes"):
        transcribe._decode_audio(b"compressed recording")
    assert len(decoded) == 2
    assert len(converted) == 1
    assert closed == [True]


def test_uploaded_playlist_cannot_read_a_second_local_file(tmp_path, monkeypatch):
    import av

    monkeypatch.chdir(tmp_path)
    (tmp_path / "marker.wav").write_bytes(wav_bytes())
    # libav autodetects concat from these bytes. Without the protocol whitelist,
    # it reads marker.wav from the working directory instead of uploaded audio.
    playlist = b"ffconcat version 1.0\nfile 'marker.wav'\n"
    with pytest.raises(av.FFmpegError):
        transcribe._decode_audio(playlist)
    # Disabling protocols must still permit the primary Python I/O input.
    assert transcribe._decode_audio(wav_bytes()).shape == (1600,)


@pytest.mark.asyncio
async def test_shutdown_drains_cancelled_worker_and_rejects_queued_and_new_requests(
    monkeypatch,
):
    started = threading.Event()
    finish = threading.Event()
    calls = []
    models = []

    class Model:
        def __init__(self, *args, **kwargs):
            models.append(weakref.ref(self))

        def transcribe(self, audio, **kwargs):
            calls.append(True)

            def segments():
                started.set()
                assert finish.wait(3), "test did not release worker"
                yield SimpleNamespace(text="done")

            return segments(), SimpleNamespace(duration=0.1, language="en")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    request = asyncio.create_task(transcribe.transcribe_voice(wav_bytes()))
    queued = closing = None
    try:
        await wait_until(started.is_set)
        queued = asyncio.create_task(transcribe.transcribe_voice(wav_bytes()))
        await asyncio.sleep(0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        closing = asyncio.create_task(transcribe.close_transcriber())
        await asyncio.sleep(0)
        assert not closing.done()
        assert models[0]() is not None
        with pytest.raises(RuntimeError, match="shutting down"):
            await transcribe.transcribe_voice(wav_bytes())
        finish.set()
        await asyncio.wait_for(closing, 3)
        with pytest.raises(RuntimeError, match="shutting down"):
            await queued
        assert calls == [True]
        assert not transcribe._workers
        assert transcribe._cached_model is None
        assert models[0]() is None
        await transcribe.close_transcriber()
    finally:
        finish.set()
        await wait_until(lambda: not transcribe._workers)
        await asyncio.gather(
            *(task for task in (request, queued, closing) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_shutdown_of_unused_transcriber_does_not_load_a_model(monkeypatch):
    def forbidden_load(*args, **kwargs):
        raise AssertionError("shutdown must not preload a model")

    monkeypatch.setattr(faster_whisper, "WhisperModel", forbidden_load)
    await transcribe.close_transcriber()
    assert transcribe._closed
    assert transcribe._cached_model is None
