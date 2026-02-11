"""Main application logic for VoiceBoard — wires all modules together."""

import atexit
import os
import platform
import signal
import sys

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer

from voiceboard.config import AppConfig, _config_dir
from voiceboard.audio import AudioRecorder, list_input_devices
from voiceboard.transcriber import RealtimeTranscriber
from voiceboard.typer import enqueue_text, ensure_ready as ensure_typer_ready
from voiceboard.hotkeys import HotkeyManager
from voiceboard.ui import MainWindow, create_tray_icon, svg_to_icon, STYLESHEET
from voiceboard.resources import TRAY_ICON_SVG, TRAY_ICON_RECORDING_SVG
from voiceboard.autostart import set_autostart

_LOCK_FILE = _config_dir() / "voiceboard.pid"


def _check_macos_accessibility() -> bool:
    """Check macOS Accessibility permission.

    Uses the simple AXIsProcessTrusted() call (no arguments) to avoid
    building CoreFoundation objects via ctypes — the complex
    AXIsProcessTrustedWithOptions approach can segfault in bundled
    (PyInstaller) apps on certain macOS versions.

    On non-macOS platforms this always returns True.
    """
    if platform.system() != "Darwin":
        return True
    try:
        import ctypes
        import ctypes.util

        # Locate ApplicationServices via find_library (respects dyld
        # shared cache on macOS 11+) with a hard-coded fallback.
        path = ctypes.util.find_library("ApplicationServices")
        if not path:
            path = (
                "/System/Library/Frameworks/ApplicationServices.framework"
                "/ApplicationServices"
            )
        lib = ctypes.cdll.LoadLibrary(path)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = bool(lib.AXIsProcessTrusted())

        # If not trusted, nudge the user by opening the right System
        # Settings pane (replaces the old AXIsProcessTrustedWithOptions
        # prompt-flag approach).
        if not trusted:
            import subprocess
            subprocess.Popen(
                ["open", "x-apple.systempreferences:"
                 "com.apple.preference.security?Privacy_Accessibility"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        return trusted
    except Exception:
        return True  # can't check — assume OK


def _kill_existing_instance() -> None:
    """If another VoiceBoard instance is running, terminate it."""
    if not _LOCK_FILE.exists():
        return
    try:
        old_pid = int(_LOCK_FILE.read_text().strip())
    except (ValueError, OSError):
        # Corrupt or unreadable lock file — just remove it
        _LOCK_FILE.unlink(missing_ok=True)
        return

    if old_pid == os.getpid():
        return  # it's us

    # Check if the process is still alive
    try:
        os.kill(old_pid, 0)  # signal 0 = existence check
    except ProcessLookupError:
        # Process is gone — stale lock file
        _LOCK_FILE.unlink(missing_ok=True)
        return
    except PermissionError:
        # Process exists but we can't query it (different user) — try to kill anyway
        pass
    except OSError as e:
        # On Windows, os.kill(pid, 0) can raise OSError with winerror 6 (invalid handle)
        # if the process doesn't exist or the handle is invalid
        if sys.platform == "win32" and getattr(e, 'winerror', None) == 6:
            # Invalid handle — process likely doesn't exist, treat as stale lock file
            _LOCK_FILE.unlink(missing_ok=True)
            return
        # For other OSErrors, re-raise
        raise

    # Terminate the old instance
    try:
        if sys.platform == "win32":
            os.kill(old_pid, signal.SIGTERM)
        else:
            os.kill(old_pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass  # already gone or not ours

    # Give it a moment to exit, then force-kill if necessary
    import time
    for _ in range(20):  # wait up to 2 seconds
        time.sleep(0.1)
        try:
            os.kill(old_pid, 0)
        except ProcessLookupError:
            break  # it's gone
        except PermissionError:
            break
        except OSError as e:
            # On Windows, os.kill(pid, 0) can raise OSError with winerror 6 (invalid handle)
            if sys.platform == "win32" and getattr(e, 'winerror', None) == 6:
                break  # process is gone
            # For other OSErrors, continue waiting
            pass
    else:
        # Still alive — force kill
        try:
            if sys.platform == "win32":
                os.kill(old_pid, signal.SIGTERM)
            else:
                os.kill(old_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    _LOCK_FILE.unlink(missing_ok=True)


def _write_pid_file() -> None:
    """Write current PID to the lock file."""
    _LOCK_FILE.write_text(str(os.getpid()))


def _remove_pid_file() -> None:
    """Remove the lock file on exit."""
    try:
        if _LOCK_FILE.exists() and _LOCK_FILE.read_text().strip() == str(os.getpid()):
            _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


class VoiceBoardApp:
    """Core application controller."""

    def __init__(self):
        self.config = AppConfig.load()
        self.recorder = AudioRecorder()
        self.transcriber = RealtimeTranscriber(
            api_key=self.config.soniox_api_key,
            language=self.config.language,
            translation_language=getattr(self.config, "translation_language", "") or "",
        )
        self.hotkeys = HotkeyManager()
        self._recording = False

    @staticmethod
    def _diag(msg: str) -> None:
        """Write a diagnostic line to stderr (survives crashes)."""
        try:
            sys.stderr.write(f"[VoiceBoard] {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass

    def run(self) -> int:
        """Run the application."""
        self._diag("startup: begin")

        # Ensure only one instance runs at a time
        _kill_existing_instance()
        _write_pid_file()
        atexit.register(_remove_pid_file)

        self._diag("startup: creating QApplication")
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName("VoiceBoard")
        self.qt_app.setQuitOnLastWindowClosed(False)
        self._diag("startup: applying stylesheet")
        self.qt_app.setStyleSheet(STYLESHEET)

        # Create main window
        self._diag("startup: creating main window")
        self.window = MainWindow()
        self.window.load_config(self.config)

        # Populate microphone list
        self._diag("startup: scanning microphones")
        self._refresh_mic_list()
        self.window.mic_refresh_btn.clicked.connect(self._refresh_mic_list)

        # Create system tray
        self._diag("startup: creating system tray")
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

        # Suspend global hotkeys while the user is capturing a new shortcut
        # to prevent the old hotkey from firing during capture.
        self.window.toggle_input.capture_started.connect(self.hotkeys.suspend)
        self.window.toggle_input.capture_ended.connect(self.hotkeys.resume)
        self.window.ptt_input.capture_started.connect(self.hotkeys.suspend)
        self.window.ptt_input.capture_ended.connect(self.hotkeys.resume)
        # Avoid triggering expensive settings save/hotkey restart on every
        # keystroke while searching the language dropdown.
        self.window.language_input.activated.connect(self._schedule_save)
        self.window.language_input.lineEdit().editingFinished.connect(self._schedule_save)
        self.window.translation_language_input.activated.connect(self._schedule_save)
        self.window.translation_language_input.lineEdit().editingFinished.connect(
            self._schedule_save
        )
        self.window.auto_start_cb.stateChanged.connect(self._schedule_save)
        self.window.typing_mode_combo.currentIndexChanged.connect(self._schedule_save)
        self.window.mic_combo.currentIndexChanged.connect(self._schedule_save)
        self.window.mic_combo.currentIndexChanged.connect(self._on_mic_changed)

        # Connect bridge signals (for thread-safe updates from hotkeys)
        self.window.signals.toggle_signal.connect(self._on_toggle)
        self.window.signals.ptt_press_signal.connect(self._on_ptt_press)
        self.window.signals.ptt_release_signal.connect(self._on_ptt_release)
        self.window.signals.transcription_text.connect(self._on_transcription_text)
        self.window.signals.transcription_error.connect(self._on_transcription_error)

        # Audio level callback
        self.recorder.on_level = lambda level: self.window.signals.audio_level.emit(level)

        # Audio chunk callback — stream PCM to the realtime transcriber
        self.recorder.on_audio_chunk = self._on_audio_chunk

        # Transcriber callbacks — emit Qt signals for thread safety
        self.transcriber.on_text = lambda text, bs, has_final, final_text: self.window.signals.transcription_text.emit(text, bs, has_final, final_text)
        self.transcriber.on_error = lambda err: self.window.signals.transcription_error.emit(err)

        # On macOS, check accessibility BEFORE starting pynput — pynput's
        # CGEventTap will segfault if the process is not trusted.
        self._diag("startup: checking accessibility")
        self._macos_accessible = True
        if not _check_macos_accessibility():
            self._macos_accessible = False
            self.window.show_warning(
                "⚠️ <b>Accessibility permission required</b><br>"
                "Global hotkeys won't work until VoiceBoard is allowed in "
                "<b>System Settings → Privacy &amp; Security → Accessibility</b>."
            )

        # Setup hotkeys (skipped on macOS when untrusted — retried by timer)
        self._diag("startup: setting up hotkeys")
        if self._macos_accessible:
            self._setup_hotkeys()
        elif platform.system() == "Darwin":
            # Poll for accessibility permission every 3 seconds; start
            # hotkeys as soon as the user grants access.
            self._accessibility_timer = QTimer()
            self._accessibility_timer.setInterval(3000)
            self._accessibility_timer.timeout.connect(self._retry_accessibility)
            self._accessibility_timer.start()

        # Start/stop microphone preview when settings page opens/closes
        self.window.settings_page.opened.connect(self._on_settings_opened)
        self.window.settings_page.closed.connect(self._on_settings_closed)

        # Show or minimize — start hidden when launched by OS autostart
        if "--autostart" in sys.argv:
            self.window.hide()
        else:
            self.window.show()

        self._diag("startup: complete — entering event loop")
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
        # Only restart preview if the settings page is currently visible
        if self.window._stack.currentIndex() == 1:
            self._stop_mic_preview()
            self._start_mic_preview()

    def _stop_mic_preview(self) -> None:
        """Stop the microphone preview stream."""
        self.recorder.stop_preview()

    def _on_settings_opened(self) -> None:
        """Start mic preview when the settings page is shown."""
        if not self._recording:
            self._start_mic_preview()

    def _on_settings_closed(self) -> None:
        """Stop mic preview when leaving the settings page."""
        if not self._recording:
            self._stop_mic_preview()

    def _retry_accessibility(self) -> None:
        """Periodically re-check macOS Accessibility permission.

        Once granted, start global hotkeys and hide the warning banner.
        """
        if _check_macos_accessibility():
            self._macos_accessible = True
            self._accessibility_timer.stop()
            self.window.hide_warning()
            self._setup_hotkeys()

    def _refresh_mic_list(self) -> None:
        """Re-scan audio input devices and update the UI dropdown."""
        try:
            devices = list_input_devices()
        except Exception:
            devices = []
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
        if not self.config.soniox_api_key:
            self.window.signals.status_update.emit(
                "⚠️ Please set your Soniox API key in Settings."
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
        """Stop recording and disconnect from the Soniox API.

        Sends a finalize message so the last non-final tokens are confirmed,
        then signals end-of-audio and schedules cleanup.
        """
        self._recording = False

        self.window.set_recording_state(False)
        self.window.signals.status_update.emit("Stopped — text available below")
        self.tray.setIcon(svg_to_icon(TRAY_ICON_SVG))
        self.tray.setToolTip("VoiceBoard — Voice Keyboard")

        # Stop the recorder — no more audio will be captured.
        self.recorder.stop()

        # Finalize any pending non-final tokens, then signal end-of-audio.
        self.transcriber.finalize()
        self.transcriber.send_eof()

        # Give the server time to send back final tokens before closing.
        QTimer.singleShot(1500, self._finish_stop)

    def _finish_stop(self) -> None:
        """Delayed cleanup — close the transcriber after finalization
        has had time to be processed."""
        self.transcriber.stop(blocking=False)

    def _on_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Forward audio chunk from the recorder to the transcriber."""
        self.transcriber.send_audio(pcm_bytes)

    def _on_transcription_text(
        self,
        text: str,
        backspace_count: int,
        has_final: bool = True,
        final_text: str = "",
    ) -> None:
        """Handle transcription text — correct non-final text and type new text.

        *backspace_count* characters of previously typed non-final text are
        erased first, then *text* (final + new non-final) is typed.

        Typing is skipped when the VoiceBoard window itself is focused to
        avoid injecting keystrokes into our own UI (which can crash the app).
        Respects typing_mode: realtime (always type, with backspaces); slow
        (only type final text, no backspaces); none (never type).
        """
        self.window.update_live_text(text, backspace_count)
        if self.window.isActiveWindow():
            return
        mode = getattr(self.config, "typing_mode", "realtime")
        if mode == "none":
            return
        if mode == "slow":
            if not has_final or not final_text:
                return
            enqueue_text(final_text, 0)
            return
        enqueue_text(text, backspace_count)

    def _on_transcription_error(self, error: str) -> None:
        """Handle transcription error."""
        self.window.signals.status_update.emit(f"❌ Error: {error[:80]}")

    def _schedule_save(self) -> None:
        """Restart the debounce timer — auto-save will fire after the delay."""
        self._save_timer.start()

    def _on_save(self) -> None:
        """Save settings from UI to config file (called automatically on change)."""
        previous_toggle = self.config.toggle_shortcut
        previous_ptt = self.config.ptt_shortcut

        self.window.save_to_config(self.config)
        self.config.save()

        # Update transcriber with new settings
        self.transcriber.update_api_key(self.config.soniox_api_key)
        self.transcriber.update_language(self.config.language)
        self.transcriber.update_translation_language(
            getattr(self.config, "translation_language", "") or ""
        )

        # Sync OS auto-start with the config setting
        set_autostart(self.config.auto_start)

        # Restart hotkeys only when shortcuts changed. Re-registering on
        # every unrelated settings save causes unnecessary UI hiccups.
        shortcuts_changed = (
            self.config.toggle_shortcut != previous_toggle
            or self.config.ptt_shortcut != previous_ptt
        )
        if shortcuts_changed:
            # Skip on macOS when Accessibility permission has not been granted
            # yet — pynput will segfault if we try to create a CGEventTap.
            try:
                self.hotkeys.stop()
                if self._macos_accessible:
                    self._setup_hotkeys()
            except Exception:
                pass  # don't let a hotkey error crash the app
