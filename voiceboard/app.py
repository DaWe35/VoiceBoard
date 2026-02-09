"""Main application logic for VoiceBoard — wires all modules together."""

import sys

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer

from voiceboard.config import AppConfig
from voiceboard.audio import AudioRecorder, list_input_devices
from voiceboard.transcriber import RealtimeTranscriber
from voiceboard.typer import enqueue_text, ensure_ready as ensure_typer_ready
from voiceboard.hotkeys import HotkeyManager
from voiceboard.ui import MainWindow, create_tray_icon, svg_to_icon, STYLESHEET
from voiceboard.resources import TRAY_ICON_SVG, TRAY_ICON_RECORDING_SVG


class VoiceBoardApp:
    """Core application controller."""

    def __init__(self):
        self.config = AppConfig.load()
        self.recorder = AudioRecorder()
        self.transcriber = RealtimeTranscriber(
            api_key=self.config.openai_api_key,
            model=self.config.model,
            language=self.config.language,
        )
        self.hotkeys = HotkeyManager()
        self._recording = False

    def run(self) -> int:
        """Run the application."""
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName("VoiceBoard")
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.qt_app.setStyleSheet(STYLESHEET)

        # Create main window
        self.window = MainWindow()
        self.window.load_config(self.config)

        # Populate microphone list
        self._refresh_mic_list()
        self.window.mic_refresh_btn.clicked.connect(self._refresh_mic_list)

        # Create system tray
        self.tray = create_tray_icon(self.qt_app, self.window)

        # Connect UI signals
        self.window.record_btn.clicked.connect(self._on_record_button)

        # Auto-save: debounce timer so rapid edits don't thrash disk
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)  # 500 ms debounce
        self._save_timer.timeout.connect(self._on_save)

        # Connect every settings widget to trigger auto-save
        self.window.api_key_input.textChanged.connect(self._schedule_save)
        self.window.toggle_input.shortcut_changed.connect(self._schedule_save)
        self.window.ptt_input.shortcut_changed.connect(self._schedule_save)
        self.window.language_input.currentTextChanged.connect(self._schedule_save)
        self.window.start_minimized_cb.stateChanged.connect(self._schedule_save)
        self.window.mic_combo.currentIndexChanged.connect(self._schedule_save)
        self.window.mic_combo.currentIndexChanged.connect(self._on_mic_changed)

        # Connect bridge signals (for thread-safe updates from hotkeys)
        self.window.signals.toggle_signal.connect(self._on_toggle)
        self.window.signals.ptt_press_signal.connect(self._on_ptt_press)
        self.window.signals.ptt_release_signal.connect(self._on_ptt_release)
        self.window.signals.transcription_done.connect(self._on_transcription_done)
        self.window.signals.transcription_delta.connect(self._on_transcription_delta)
        self.window.signals.transcription_turn_started.connect(self._on_turn_started)
        self.window.signals.transcription_error.connect(self._on_transcription_error)

        # Audio level callback
        self.recorder.on_level = lambda level: self.window.signals.audio_level.emit(level)

        # Audio chunk callback — stream PCM to the realtime transcriber
        self.recorder.on_audio_chunk = self._on_audio_chunk

        # Transcriber callbacks — emit Qt signals for thread safety
        self.transcriber.on_delta = lambda delta: self.window.signals.transcription_delta.emit(delta)
        self.transcriber.on_completed = lambda text: self.window.signals.transcription_done.emit(text)
        self.transcriber.on_error = lambda err: self.window.signals.transcription_error.emit(err)
        self.transcriber.on_turn_started = lambda: self.window.signals.transcription_turn_started.emit()

        # Setup hotkeys
        self._setup_hotkeys()

        # Start microphone preview (level meter only, no transcription)
        self._start_mic_preview()

        # Show or minimize
        if self.config.start_minimized:
            self.window.hide()
        else:
            self.window.show()

        return self.qt_app.exec()

    def _start_mic_preview(self) -> None:
        """Start the microphone preview stream for level monitoring."""
        try:
            selected = self.window.selected_device_index()
            self.recorder.device = int(selected) if selected else None
            self.recorder.start_preview()
        except Exception:
            pass  # silently ignore — preview is non-critical

    def _on_mic_changed(self) -> None:
        """Restart the mic preview on the newly selected device."""
        if self._recording:
            return  # don't disrupt an active recording session
        self._stop_mic_preview()
        self._start_mic_preview()

    def _stop_mic_preview(self) -> None:
        """Stop the microphone preview stream."""
        self.recorder.stop_preview()

    def _refresh_mic_list(self) -> None:
        """Re-scan audio input devices and update the UI dropdown."""
        devices = list_input_devices()
        self.window.populate_mic_list(devices, self.config.input_device)

    def _setup_hotkeys(self) -> None:
        """Configure and start global hotkey listener."""
        self.hotkeys.set_shortcuts(
            self.config.toggle_shortcut,
            self.config.ptt_shortcut,
        )
        self.hotkeys.on_toggle = lambda: self.window.signals.toggle_signal.emit()
        self.hotkeys.on_ptt_press = lambda: self.window.signals.ptt_press_signal.emit()
        self.hotkeys.on_ptt_release = lambda: self.window.signals.ptt_release_signal.emit()
        self.hotkeys.start()

    def _on_record_button(self) -> None:
        """Handle the big record/stop button click."""
        self._on_toggle()

    def _on_toggle(self) -> None:
        """Toggle recording on/off."""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _on_ptt_press(self) -> None:
        """Push-to-talk: start recording on press."""
        if not self._recording:
            self._start_recording()

    def _on_ptt_release(self) -> None:
        """Push-to-talk: stop on release."""
        if self._recording:
            self._stop_recording()

    def _start_recording(self) -> None:
        """Begin audio capture and realtime transcription."""
        if not self.config.openai_api_key:
            self.window.signals.status_update.emit(
                "⚠️ Please set your OpenAI API key in Settings."
            )
            return

        # Eagerly initialise the typer so that any platform permission
        # dialog (e.g. Wayland RemoteDesktop portal) appears now, not
        # in the middle of a transcription.
        ensure_typer_ready()

        self._recording = True
        self.window.set_recording_state(True)
        self.tray.setIcon(svg_to_icon(TRAY_ICON_RECORDING_SVG))
        self.tray.setToolTip("VoiceBoard — Recording...")

        # Check if the selected mic changed since the preview started
        selected = self.window.selected_device_index()
        new_device = int(selected) if selected else None
        if new_device != self.recorder.device:
            # Restart the stream on the new device
            self._stop_mic_preview()
            self.recorder.device = new_device

        # Start the realtime WebSocket transcription session
        self.transcriber.start()
        # Start capturing audio (chunks will be forwarded to the transcriber)
        self.recorder.start()

    def _stop_recording(self) -> None:
        """Stop recording and disconnect from the Realtime API."""
        self._recording = False
        self.recorder.stop()
        self.transcriber.stop()

        self.window.set_recording_state(False)
        self.tray.setIcon(svg_to_icon(TRAY_ICON_SVG))
        self.tray.setToolTip("VoiceBoard — Voice Keyboard")

        # Restart mic preview so the level meter keeps showing
        self._start_mic_preview()

    def _on_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Forward audio chunk from the recorder to the transcriber."""
        self.transcriber.send_audio(pcm_bytes)

    def _on_transcription_delta(self, delta: str) -> None:
        """Handle incremental transcription text — type it in real-time
        and update the live preview."""
        self.window.append_live_text(delta)
        # Queue the delta for typing on a persistent background thread.
        # (A single worker thread avoids the Windows bug where spawning a
        # new thread per delta causes the pynput keyboard hook to time out
        # and drop injected keystrokes after the first word.)
        enqueue_text(delta)

    def _on_turn_started(self) -> None:
        """A new speech turn was detected — reset the live preview."""
        self.window.reset_live_text()

    def _on_transcription_done(self, text: str) -> None:
        """Handle completed transcription — update status bar."""
        self.window.signals.status_update.emit(
            f"✅ \"{text[:60]}{'…' if len(text) > 60 else ''}\""
        )

    def _on_transcription_error(self, error: str) -> None:
        """Handle transcription error."""
        self.window.signals.status_update.emit(f"❌ Error: {error[:80]}")

    def _schedule_save(self) -> None:
        """Restart the debounce timer — auto-save will fire after the delay."""
        self._save_timer.start()

    def _on_save(self) -> None:
        """Save settings from UI to config file (called automatically on change)."""
        self.window.save_to_config(self.config)
        self.config.save()

        # Update transcriber with new settings
        self.transcriber.update_api_key(self.config.openai_api_key)
        self.transcriber.update_language(self.config.language)

        # Restart hotkeys with new shortcuts
        self.hotkeys.stop()
        self._setup_hotkeys()
