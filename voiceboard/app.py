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
    """Check macOS Accessibility permission, prompting the user if missing.

    Calls AXIsProcessTrustedWithOptions with the prompt flag so macOS
    automatically shows a system dialog asking the user to grant access.
    On non-macOS platforms this always returns True.
    """
    if platform.system() != "Darwin":
        return True
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        CoreFoundation = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("CoreFoundation")
        )
        AppServices = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework"
            "/ApplicationServices"
        )

        # Set up objc runtime calls
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        # Create the CFString key "AXTrustedCheckOptionPrompt"
        CoreFoundation.CFStringCreateWithCString.restype = ctypes.c_void_p
        CoreFoundation.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
        ]
        prompt_key = CoreFoundation.CFStringCreateWithCString(
            None, b"AXTrustedCheckOptionPrompt", 0,
        )

        # kCFBooleanTrue
        kCFBooleanTrue = ctypes.c_void_p.in_dll(CoreFoundation, "kCFBooleanTrue")

        # Build a CFDictionary: {kAXTrustedCheckOptionPrompt: true}
        CoreFoundation.CFDictionaryCreate.restype = ctypes.c_void_p
        CoreFoundation.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        keys = (ctypes.c_void_p * 1)(prompt_key)
        values = (ctypes.c_void_p * 1)(kCFBooleanTrue)
        options = CoreFoundation.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.c_void_p.in_dll(CoreFoundation, "kCFTypeDictionaryKeyCallBacks"),
            ctypes.c_void_p.in_dll(CoreFoundation, "kCFTypeDictionaryValueCallBacks"),
        )

        # AXIsProcessTrustedWithOptions — shows the native macOS prompt
        AppServices.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        AppServices.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        return bool(AppServices.AXIsProcessTrustedWithOptions(options))
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
        )
        self.hotkeys = HotkeyManager()
        self._hotkeys_started = False
        self._recording = False

    def run(self) -> int:
        """Run the application."""
        # Ensure only one instance runs at a time
        _kill_existing_instance()
        _write_pid_file()
        atexit.register(_remove_pid_file)

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
        self.window.auto_start_cb.stateChanged.connect(self._schedule_save)
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
        self.transcriber.on_text = lambda text, bs: self.window.signals.transcription_text.emit(text, bs)
        self.transcriber.on_error = lambda err: self.window.signals.transcription_error.emit(err)

        # On macOS, check Accessibility before starting global listeners.
        # Starting pynput listeners before trust is granted can crash.
        can_start_hotkeys = _check_macos_accessibility()
        if can_start_hotkeys:
            self._setup_hotkeys()
        else:
            self.window.show_warning(
                "⚠️ <b>Accessibility permission required</b><br>"
                "Global hotkeys won't work until VoiceBoard is allowed in "
                "<b>System Settings → Privacy &amp; Security → Accessibility</b>."
            )

        # Start/stop microphone preview when settings page opens/closes
        self.window.settings_page.opened.connect(self._on_settings_opened)
        self.window.settings_page.closed.connect(self._on_settings_closed)

        # Show or minimize — start hidden when launched by OS autostart
        if "--autostart" in sys.argv:
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
        self._hotkeys_started = True

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

    def _on_transcription_text(self, text: str, backspace_count: int) -> None:
        """Handle transcription text — correct non-final text and type new text.

        *backspace_count* characters of previously typed non-final text are
        erased first, then *text* (final + new non-final) is typed.

        Typing is skipped when the VoiceBoard window itself is focused to
        avoid injecting keystrokes into our own UI (which can crash the app).
        """
        self.window.update_live_text(text, backspace_count)
        if not self.window.isActiveWindow():
            enqueue_text(text, backspace_count)

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
        self.transcriber.update_api_key(self.config.soniox_api_key)
        self.transcriber.update_language(self.config.language)

        # Sync OS auto-start with the config setting
        set_autostart(self.config.auto_start)

        # Restart hotkeys with new shortcuts (when running).
        if self._hotkeys_started:
            self.hotkeys.stop()
            self._setup_hotkeys()
