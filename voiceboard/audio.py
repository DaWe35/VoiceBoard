"""Audio recording module for VoiceBoard.

Captures microphone audio and streams raw PCM16 chunks at 24 kHz mono
to a callback (used to feed the OpenAI Realtime API transcriber).

The recorder always opens the device at its native/default sample rate
and resamples to 24 kHz on the fly if needed, so it works with any
hardware without manual configuration.
"""

import contextlib
import logging
import os
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

# The OpenAI Realtime API requires 24 kHz PCM16 mono.
TARGET_RATE = 24000


def list_input_devices() -> list[dict]:
    """Return a list of available audio input devices.

    Each entry is a dict with keys ``index`` (int), ``name`` (str), and
    ``channels`` (int).  Only devices that support at least one input
    channel are included.
    """
    devices: list[dict] = []
    try:
        with _suppress_stderr():
            all_devs = sd.query_devices()
        for idx, dev in enumerate(all_devs):  # type: ignore[arg-type]
            if dev.get("max_input_channels", 0) > 0:  # type: ignore[union-attr]
                devices.append({
                    "index": idx,
                    "name": dev["name"],  # type: ignore[index]
                    "channels": dev["max_input_channels"],  # type: ignore[index]
                })
    except Exception:
        log.exception("Failed to enumerate audio input devices")
    return devices


@contextlib.contextmanager
def _suppress_stderr():
    """Temporarily redirect stderr to /dev/null to silence noisy PortAudio/ALSA messages."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        os.close(devnull)
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)


def _resample_linear(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample *data* (1-D int16 array) from *src_rate* to *dst_rate*.

    Uses simple linear interpolation — good enough for speech audio where
    the target is a transcription model, not hi-fi playback.
    """
    if src_rate == dst_rate:
        return data
    ratio = dst_rate / src_rate
    src_len = len(data)
    dst_len = int(round(src_len * ratio))
    if dst_len == 0:
        return np.array([], dtype=np.int16)
    indices = np.linspace(0, src_len - 1, dst_len)
    resampled = np.interp(indices, np.arange(src_len), data.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16)


class AudioRecorder:
    """Records audio from the microphone and streams 24 kHz PCM16 chunks."""

    def __init__(self, channels: int = 1, device: Optional[int] = None):
        self.channels = channels
        self.device: Optional[int] = device  # None = system default
        self._stream: Optional[sd.InputStream] = None
        self._recording = False
        self._previewing = False
        self._device_rate: int = TARGET_RATE  # actual rate the device opened at

        # Callbacks
        self.on_audio_chunk: Optional[Callable[[bytes], None]] = None
        self.on_level: Optional[Callable[[float], None]] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_previewing(self) -> bool:
        return self._previewing

    def _open_stream(self) -> None:
        """Open the microphone input stream (shared by preview and recording)."""
        if self._stream is not None:
            return  # already open

        device = self.device  # None means system default

        # Query the device's preferred sample rate
        try:
            if device is not None:
                info = sd.query_devices(device)
            else:
                info = sd.query_devices(kind="input")
            device_rate = int(info["default_samplerate"])  # type: ignore[index]
        except Exception:
            device_rate = TARGET_RATE

        # Try the device's native rate first, then fall back to 24 kHz
        for rate in dict.fromkeys([device_rate, TARGET_RATE]):
            try:
                blocksize = int(rate * 0.1)  # 100 ms
                with _suppress_stderr():
                    self._stream = sd.InputStream(
                        device=device,
                        samplerate=rate,
                        channels=self.channels,
                        dtype="int16",
                        callback=self._audio_callback,
                        blocksize=blocksize,
                    )
                self._device_rate = rate
                break
            except sd.PortAudioError:
                log.debug("Cannot open audio at %d Hz on device %s", rate, device)
                continue
        else:
            raise RuntimeError(
                "Could not open an audio input stream. "
                "Please check your microphone / audio device."
            )

        if self._device_rate != TARGET_RATE:
            log.info(
                "Resampling audio from %d Hz → %d Hz",
                self._device_rate,
                TARGET_RATE,
            )

        self._stream.start()

    def _close_stream(self) -> None:
        """Close the microphone input stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def start_preview(self) -> None:
        """Open the mic stream for level monitoring only (no audio chunks)."""
        if self._previewing or self._recording:
            return
        self._open_stream()
        self._previewing = True

    def stop_preview(self) -> None:
        """Stop the level-monitoring preview."""
        if not self._previewing:
            return
        self._previewing = False
        if not self._recording:
            self._close_stream()

    def start(self) -> None:
        """Start recording audio from the selected (or default) microphone."""
        if self._recording:
            return

        # If preview is already running, reuse the stream; otherwise open one.
        self._open_stream()

        self._recording = True

    def stop(self) -> None:
        """Stop recording (keeps preview alive if it was active)."""
        if not self._recording:
            return
        self._recording = False
        if not self._previewing:
            self._close_stream()

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """Called by sounddevice for each audio block."""
        if not self._recording and not self._previewing:
            return

        # Report audio level for UI visualization (always, even in preview)
        if self.on_level:
            level = float(np.abs(indata).mean()) / 32768.0
            self.on_level(level)

        # Only forward PCM chunks when actually recording
        if not self._recording:
            return

        pcm = indata[:, 0] if indata.ndim > 1 else indata.ravel()

        # Resample to 24 kHz if the device opened at a different rate
        if self._device_rate != TARGET_RATE:
            pcm = _resample_linear(pcm, self._device_rate, TARGET_RATE)

        # Send raw PCM16 bytes to the transcriber
        if self.on_audio_chunk:
            self.on_audio_chunk(pcm.tobytes())
