"""Audio recording module for VoiceBoard.

Captures microphone audio at 24 kHz mono PCM16 and streams raw chunks
to a callback (used to feed the Realtime API transcriber).
"""

import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


class AudioRecorder:
    """Records audio from the microphone and streams PCM16 chunks."""

    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._stream: Optional[sd.InputStream] = None
        self._recording = False

        # Callbacks
        self.on_audio_chunk: Optional[Callable[[bytes], None]] = None
        self.on_level: Optional[Callable[[float], None]] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """Start recording audio from the default microphone."""
        if self._recording:
            return
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=self._audio_callback,
            blocksize=2400,  # 100 ms chunks at 24 kHz
        )
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

        # Send raw PCM16 bytes to the transcriber
        if self.on_audio_chunk:
            self.on_audio_chunk(indata.tobytes())

        # Report audio level for UI visualization
        if self.on_level:
            level = float(np.abs(indata).mean()) / 32768.0
            self.on_level(level)
