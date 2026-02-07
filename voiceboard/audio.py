"""Audio recording module for VoiceBoard.

Captures microphone audio at 24 kHz mono PCM16 and streams raw chunks
to a callback (used to feed the Realtime API transcriber).

If the audio device does not support 24 kHz natively, the recorder
will open the stream at a supported rate and resample to 24 kHz on
the fly so the downstream transcriber always receives 24 kHz PCM16.
"""

import logging
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

# Rates to try when the desired rate is rejected by the device,
# ordered by preference (multiples of 24 kHz first for cleaner resampling).
_FALLBACK_RATES = [48000, 44100, 16000, 8000]


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
    # Work in float32 for interpolation, then convert back to int16
    indices = np.linspace(0, src_len - 1, dst_len)
    resampled = np.interp(indices, np.arange(src_len), data.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16)


class AudioRecorder:
    """Records audio from the microphone and streams PCM16 chunks."""

    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.sample_rate = sample_rate  # desired / output rate
        self.channels = channels
        self._stream: Optional[sd.InputStream] = None
        self._recording = False
        self._device_rate: int = sample_rate  # actual device rate

        # Callbacks
        self.on_audio_chunk: Optional[Callable[[bytes], None]] = None
        self.on_level: Optional[Callable[[float], None]] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _open_stream(self, rate: int, blocksize: int) -> sd.InputStream:
        """Try to open an InputStream at the given *rate*."""
        return sd.InputStream(
            samplerate=rate,
            channels=self.channels,
            dtype="int16",
            callback=self._audio_callback,
            blocksize=blocksize,
        )

    def start(self) -> None:
        """Start recording audio from the default microphone."""
        if self._recording:
            return

        # Determine a working sample rate
        self._device_rate = self.sample_rate
        blocksize = int(self._device_rate * 0.1)  # 100 ms worth of frames

        try:
            self._stream = self._open_stream(self._device_rate, blocksize)
        except sd.PortAudioError:
            log.warning(
                "Device does not support %d Hz; trying fallback rates…",
                self._device_rate,
            )
            self._stream = None

            # Build a list of rates to try: device default first, then common rates
            rates_to_try = list(_FALLBACK_RATES)
            try:
                info = sd.query_devices(kind="input")
                default_rate = int(info["default_samplerate"])  # type: ignore[index]
                if default_rate not in rates_to_try:
                    rates_to_try.insert(0, default_rate)
            except Exception:
                pass

            for rate in rates_to_try:
                try:
                    blocksize = int(rate * 0.1)
                    self._stream = self._open_stream(rate, blocksize)
                    self._device_rate = rate
                    log.info("Opened audio stream at fallback rate %d Hz", rate)
                    break
                except sd.PortAudioError:
                    continue

            if self._stream is None:
                raise RuntimeError(
                    "Could not open an audio input stream at any supported sample rate. "
                    "Please check your microphone / audio device."
                )

        need_resample = self._device_rate != self.sample_rate
        if need_resample:
            log.info(
                "Resampling audio from %d Hz → %d Hz",
                self._device_rate,
                self.sample_rate,
            )

        self._recording = True
        self._stream.start()

    def stop(self) -> None:
        """Stop recording."""
        if not self._recording:
            return
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """Called by sounddevice for each audio block."""
        if not self._recording:
            return

        pcm = indata[:, 0] if indata.ndim > 1 else indata.ravel()

        # Resample to the desired rate if the device rate differs
        if self._device_rate != self.sample_rate:
            pcm = _resample_linear(pcm, self._device_rate, self.sample_rate)

        # Send raw PCM16 bytes to the transcriber
        if self.on_audio_chunk:
            self.on_audio_chunk(pcm.tobytes())

        # Report audio level for UI visualization
        if self.on_level:
            level = float(np.abs(indata).mean()) / 32768.0
            self.on_level(level)
