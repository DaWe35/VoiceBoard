"""Modern UI for VoiceBoard using PySide6 (Qt6)."""

import sys
from typing import Optional
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSystemTrayIcon,
    QMenu,
    QGroupBox,
    QFormLayout,
    QComboBox,
    QCheckBox,
    QMessageBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QSize, Signal, QObject, QTimer
from PySide6.QtGui import QIcon, QPixmap, QFont, QAction, QPainter, QColor, QPen, QKeySequence

from voiceboard.resources import TRAY_ICON_SVG, TRAY_ICON_RECORDING_SVG


def svg_to_icon(svg_str: str) -> QIcon:
    """Convert SVG string to QIcon."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray

    renderer = QSvgRenderer(QByteArray(svg_str.encode()))
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


# Stylesheet for the entire application — modern dark theme
STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', 'SF Pro Display', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #2d2d4a;
    border-radius: 10px;
    margin-top: 14px;
    padding: 18px 14px 14px 14px;
    font-weight: bold;
    font-size: 14px;
    color: #b0b0d0;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 6px;
}

QLabel {
    color: #c0c0e0;
    font-size: 13px;
}

QLineEdit {
    background-color: #16213e;
    border: 1px solid #2d2d4a;
    border-radius: 6px;
    padding: 8px 12px;
    color: #e0e0e0;
    font-size: 13px;
    selection-background-color: #6C63FF;
}

QLineEdit:focus {
    border: 1px solid #6C63FF;
}

QComboBox {
    background-color: #16213e;
    border: 1px solid #2d2d4a;
    border-radius: 6px;
    padding: 8px 12px;
    color: #e0e0e0;
    font-size: 13px;
}

QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}

QComboBox QAbstractItemView {
    background-color: #16213e;
    border: 1px solid #2d2d4a;
    color: #e0e0e0;
    selection-background-color: #6C63FF;
}

QPushButton {
    background-color: #6C63FF;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #7B73FF;
}

QPushButton:pressed {
    background-color: #5A52E0;
}

QPushButton:disabled {
    background-color: #3a3a5a;
    color: #666680;
}

QPushButton#saveBtn {
    background-color: #4CAF50;
}

QPushButton#saveBtn:hover {
    background-color: #5CBF60;
}

QCheckBox {
    color: #c0c0e0;
    spacing: 8px;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #2d2d4a;
    background-color: #16213e;
}

QCheckBox::indicator:checked {
    background-color: #6C63FF;
    border-color: #6C63FF;
}

#statusLabel {
    font-size: 14px;
    color: #a0a0c0;
    padding: 4px;
}

#statusLabel[recording="true"] {
    color: #FF6B6B;
    font-weight: bold;
}

#levelBar {
    background-color: #16213e;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
}
"""


class RecordButton(QPushButton):
    """Large round record/stop button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recording = False
        self.setFixedSize(120, 120)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()

    @property
    def recording(self) -> bool:
        return self._recording

    @recording.setter
    def recording(self, value: bool) -> None:
        self._recording = value
        self._update_style()

    def _update_style(self) -> None:
        if self._recording:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #FF4444;
                    border: 4px solid #FF6B6B;
                    border-radius: 60px;
                    font-size: 14px;
                    font-weight: bold;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #FF5555;
                    border-color: #FF8888;
                }
            """)
            self.setText("⏹ STOP")
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #6C63FF;
                    border: 4px solid #8B83FF;
                    border-radius: 60px;
                    font-size: 14px;
                    font-weight: bold;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #7B73FF;
                    border-color: #9B93FF;
                }
            """)
            self.setText("🎤 START")


class AudioLevelWidget(QWidget):
    """Simple audio level meter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setMinimumWidth(200)
        self._level = 0.0
        self._recording = False

    @property
    def recording(self) -> bool:
        return self._recording

    @recording.setter
    def recording(self, value: bool) -> None:
        self._recording = value
        self.update()

    def set_level(self, level: float) -> None:
        self._level = min(1.0, max(0.0, level * 8))  # amplify for visibility
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#16213e"))
        painter.drawRoundedRect(self.rect(), 4, 4)

        # Level fill
        if self._level > 0:
            w = int(self.width() * self._level)
            if self._recording:
                color = QColor("#FF4444")
            else:
                color = QColor("#6C63FF") if self._level < 0.7 else QColor("#FF6B6B")
            painter.setBrush(color)
            painter.drawRoundedRect(0, 0, w, self.height(), 4, 4)

        painter.end()


class ShortcutCaptureInput(QLineEdit):
    """A line-edit that captures key combinations when focused.

    Supports:
      - Modifier combos: Ctrl+Shift+V
      - Any-key combos: Space+B, A+S
      - Double-tap: press the same single key twice quickly → "2× Ctrl"
      - Single non-modifier keys: F5, Pause, etc.

    Click the field → it enters "listening" mode → press a key combo →
    it gets recorded.  Press Escape to clear.

    Shortcut format stored in config:
      - Regular combo: ``<ctrl>+<shift>+v``
      - Double-tap:    ``2x<ctrl>``
    """

    shortcut_changed = Signal(str)  # emits the config-format string

    # Double-tap detection window (seconds)
    _DOUBLE_TAP_MS = 400

    # Qt key codes that are modifier-only
    _MODIFIER_KEYS = {
        Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_AltGr,
        Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R,
    }

    # Qt key → (display name, config token)
    _KEY_NAMES: dict[int, tuple[str, str]] = {
        # Modifiers (used when they appear in combos or double-taps)
        Qt.Key_Control: ("Ctrl", "<ctrl>"),
        Qt.Key_Shift: ("Shift", "<shift>"),
        Qt.Key_Alt: ("Alt", "<alt>"),
        Qt.Key_AltGr: ("Alt", "<alt>"),
        Qt.Key_Meta: ("Super", "<super>"),
        Qt.Key_Super_L: ("Super", "<super>"),
        Qt.Key_Super_R: ("Super", "<super>"),
        # Function keys
        Qt.Key_F1: ("F1", "<f1>"), Qt.Key_F2: ("F2", "<f2>"),
        Qt.Key_F3: ("F3", "<f3>"), Qt.Key_F4: ("F4", "<f4>"),
        Qt.Key_F5: ("F5", "<f5>"), Qt.Key_F6: ("F6", "<f6>"),
        Qt.Key_F7: ("F7", "<f7>"), Qt.Key_F8: ("F8", "<f8>"),
        Qt.Key_F9: ("F9", "<f9>"), Qt.Key_F10: ("F10", "<f10>"),
        Qt.Key_F11: ("F11", "<f11>"), Qt.Key_F12: ("F12", "<f12>"),
        # Special keys
        Qt.Key_Space: ("Space", "<space>"),
        Qt.Key_Return: ("Enter", "<enter>"),
        Qt.Key_Enter: ("Enter", "<enter>"),
        Qt.Key_Tab: ("Tab", "<tab>"),
        Qt.Key_Backspace: ("Backspace", "<backspace>"),
        Qt.Key_Delete: ("Delete", "<delete>"),
        Qt.Key_Home: ("Home", "<home>"),
        Qt.Key_End: ("End", "<end>"),
        Qt.Key_PageUp: ("PageUp", "<page_up>"),
        Qt.Key_PageDown: ("PageDown", "<page_down>"),
        Qt.Key_Up: ("Up", "<up>"),
        Qt.Key_Down: ("Down", "<down>"),
        Qt.Key_Left: ("Left", "<left>"),
        Qt.Key_Right: ("Right", "<right>"),
        Qt.Key_Insert: ("Insert", "<insert>"),
        Qt.Key_Pause: ("Pause", "<pause>"),
        Qt.Key_Print: ("PrintScreen", "<print_screen>"),
        Qt.Key_ScrollLock: ("ScrollLock", "<scroll_lock>"),
        Qt.Key_CapsLock: ("CapsLock", "<caps_lock>"),
        Qt.Key_NumLock: ("NumLock", "<num_lock>"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setPlaceholderText("Click here, then press a shortcut…")
        self._shortcut_str = ""       # config-format string
        self._listening = False

        # State for multi-key capture
        self._held_keys: list[int] = []  # keys currently held, in press order
        self._finalize_timer = QTimer(self)
        self._finalize_timer.setSingleShot(True)
        self._finalize_timer.timeout.connect(self._finalize_combo)

        # State for double-tap detection
        self._last_single_key: Optional[int] = None  # the key from the last single press
        self._last_single_time: float = 0.0           # monotonic timestamp
        self._double_tap_timer = QTimer(self)
        self._double_tap_timer.setSingleShot(True)
        self._double_tap_timer.timeout.connect(self._finalize_single_key)

    def shortcut_string(self) -> str:
        """Return the stored config-format shortcut string."""
        return self._shortcut_str

    def set_shortcut_string(self, shortcut_str: str) -> None:
        """Set the shortcut from a config-format string and update display."""
        self._shortcut_str = shortcut_str
        if shortcut_str:
            self.setText(self._shortcut_to_display(shortcut_str))
        else:
            self.setText("")
        self._listening = False
        self._update_style()

    def _update_style(self) -> None:
        if self._listening:
            self.setStyleSheet(
                "QLineEdit { border: 2px solid #6C63FF; background-color: #1e1e3e; "
                "color: #6C63FF; font-weight: bold; }"
            )
        else:
            self.setStyleSheet("")

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._listening = True
        self._held_keys.clear()
        self._last_single_key = None
        self._finalize_timer.stop()
        self._double_tap_timer.stop()
        self.setText("Press a key combination…")
        self._update_style()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._listening = False
        self._finalize_timer.stop()
        self._double_tap_timer.stop()
        if self._shortcut_str:
            self.setText(self._shortcut_to_display(self._shortcut_str))
        else:
            self.setText("")
        self._update_style()

    def _key_info(self, qt_key: int, event=None) -> tuple[str, str] | None:
        """Return (display_name, config_token) for a Qt key code."""
        if qt_key in self._KEY_NAMES:
            return self._KEY_NAMES[qt_key]
        # Try the event text for printable characters
        if event is not None:
            text = event.text()
            if text and text.isprintable():
                ch = text.lower()
                return (ch.upper(), ch)
        # Last resort: QKeySequence
        seq = QKeySequence(qt_key)
        name = seq.toString()
        if name:
            return (name, f"<{name.lower()}>")
        return None

    def keyPressEvent(self, event) -> None:
        if not self._listening:
            return

        key = event.key()

        # Escape → clear shortcut
        if key == Qt.Key_Escape:
            self._shortcut_str = ""
            self._held_keys.clear()
            self._last_single_key = None
            self._finalize_timer.stop()
            self._double_tap_timer.stop()
            self.setText("")
            self.shortcut_changed.emit("")
            self.clearFocus()
            return

        # Ignore auto-repeat
        if event.isAutoRepeat():
            return

        # Cancel any pending single-key finalization (we're building a combo)
        self._double_tap_timer.stop()

        # Track this key
        if key not in self._held_keys:
            self._held_keys.append(key)

        # Show live preview of keys being held
        self._show_held_preview()

        # Restart the finalize timer — we wait a bit after the last keypress
        # to allow the user to press additional keys
        self._finalize_timer.start(300)

    def keyReleaseEvent(self, event) -> None:
        if not self._listening:
            return
        if event.isAutoRepeat():
            return
        # We don't remove from _held_keys on release — we want to capture
        # the full set of keys that were held simultaneously.
        # The finalize timer handles committing the combo.

    def _show_held_preview(self) -> None:
        """Show a live preview of the keys currently being held."""
        parts = []
        for k in self._held_keys:
            info = self._key_info(k)
            if info:
                parts.append(info[0])
        if parts:
            self.setText(" + ".join(parts) + " …")

    def _finalize_combo(self) -> None:
        """Called after keys stop being pressed — commit the captured combo."""
        if not self._held_keys:
            return

        import time

        keys = list(self._held_keys)
        self._held_keys.clear()

        # Single key press — might be a double-tap
        if len(keys) == 1:
            key = keys[0]
            now = time.monotonic()

            if (self._last_single_key == key
                    and (now - self._last_single_time) * 1000 < self._DOUBLE_TAP_MS):
                # Double-tap detected!
                self._last_single_key = None
                info = self._key_info(key)
                if info:
                    disp, token = info
                    self._shortcut_str = f"2x{token}"
                    self.setText(f"{disp} × 2")
                    self.shortcut_changed.emit(self._shortcut_str)
                    self.clearFocus()
                return

            # First single press — wait to see if a second tap comes
            self._last_single_key = key
            self._last_single_time = now
            info = self._key_info(key)
            if info:
                self.setText(f"{info[0]}  (tap again for double-tap, or wait…)")
            self._double_tap_timer.start(self._DOUBLE_TAP_MS)
            return

        # Multi-key combo — commit immediately
        self._last_single_key = None
        self._commit_combo(keys)

    def _finalize_single_key(self) -> None:
        """Double-tap window expired — commit as a single-key shortcut."""
        if self._last_single_key is None:
            return
        key = self._last_single_key
        self._last_single_key = None
        self._commit_combo([key])

    def _commit_combo(self, keys: list[int]) -> None:
        """Build the config string from a list of Qt key codes and commit."""
        display_parts = []
        token_parts = []

        # Sort: modifiers first, then other keys, preserving order within groups
        modifier_keys = []
        regular_keys = []
        for k in keys:
            if k in self._MODIFIER_KEYS:
                modifier_keys.append(k)
            else:
                regular_keys.append(k)

        for k in modifier_keys + regular_keys:
            info = self._key_info(k)
            if info:
                display_parts.append(info[0])
                token_parts.append(info[1])

        if not token_parts:
            return

        display_text = " + ".join(display_parts)
        config_text = "+".join(token_parts)

        self._shortcut_str = config_text
        self.setText(display_text)
        self.shortcut_changed.emit(config_text)
        self.clearFocus()

    @staticmethod
    def _shortcut_to_display(shortcut_str: str) -> str:
        """Convert a config-format shortcut string to a nice display label."""
        if not shortcut_str:
            return ""

        # Handle double-tap format: "2x<token>"
        if shortcut_str.startswith("2x"):
            inner = shortcut_str[2:]
            inner_disp = ShortcutCaptureInput._token_to_display(inner)
            return f"{inner_disp} × 2"

        parts = shortcut_str.split("+")
        display_parts = []
        for part in parts:
            display_parts.append(ShortcutCaptureInput._token_to_display(part.strip()))
        return " + ".join(display_parts)

    @staticmethod
    def _token_to_display(token: str) -> str:
        """Convert a single config token to display text."""
        _map = {
            "<ctrl>": "Ctrl", "<shift>": "Shift", "<alt>": "Alt",
            "<super>": "Super", "<cmd>": "Super",
            "<space>": "Space", "<enter>": "Enter", "<tab>": "Tab",
            "<backspace>": "Backspace", "<delete>": "Delete",
            "<home>": "Home", "<end>": "End",
            "<page_up>": "PageUp", "<page_down>": "PageDown",
            "<up>": "Up", "<down>": "Down", "<left>": "Left", "<right>": "Right",
            "<insert>": "Insert", "<pause>": "Pause",
            "<print_screen>": "PrintScreen", "<scroll_lock>": "ScrollLock",
            "<caps_lock>": "CapsLock", "<num_lock>": "NumLock",
        }
        for i in range(1, 13):
            _map[f"<f{i}>"] = f"F{i}"

        lower = token.lower()
        if lower in _map:
            return _map[lower]
        if len(token) == 1:
            return token.upper()
        return token


class SignalBridge(QObject):
    """Bridge for thread-safe signal communication."""
    toggle_signal = Signal()
    ptt_press_signal = Signal()
    ptt_release_signal = Signal()
    transcription_done = Signal(str)
    transcription_delta = Signal(str)
    transcription_turn_started = Signal()
    transcription_error = Signal(str)
    audio_level = Signal(float)
    status_update = Signal(str)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.signals = SignalBridge()
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("VoiceBoard")
        self.setMinimumSize(420, 680)
        self.setMaximumWidth(500)
        self.setWindowIcon(svg_to_icon(TRAY_ICON_SVG))

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Header ──
        header = QLabel("🎙️ VoiceBoard")
        header.setAlignment(Qt.AlignCenter)
        header.setFont(QFont("Segoe UI", 22, QFont.Bold))
        header.setStyleSheet("color: #6C63FF; margin-bottom: 4px;")
        layout.addWidget(header)

        subtitle = QLabel("Voice-to-text keyboard powered by OpenAI")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #7070a0; font-size: 12px; margin-bottom: 8px;")
        layout.addWidget(subtitle)

        # ── Record Button ──
        btn_container = QHBoxLayout()
        btn_container.setAlignment(Qt.AlignCenter)
        self.record_btn = RecordButton()
        btn_container.addWidget(self.record_btn)
        layout.addLayout(btn_container)

        # ── Audio Level ──
        self.audio_level = AudioLevelWidget()
        layout.addWidget(self.audio_level)

        # ── Status ──
        self.status_label = QLabel("Ready — press Start or use a shortcut")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # ── Live Transcription Preview ──
        self.live_preview = QLabel("")
        self.live_preview.setObjectName("livePreview")
        self.live_preview.setAlignment(Qt.AlignCenter)
        self.live_preview.setWordWrap(True)
        self.live_preview.setMinimumHeight(40)
        self.live_preview.setStyleSheet(
            "color: #b0b0d0; font-size: 15px; font-style: italic; "
            "padding: 8px; background-color: #16213e; border-radius: 8px;"
        )
        self.live_preview.hide()
        layout.addWidget(self.live_preview)

        # ── API Key ──
        api_group = QGroupBox("OpenAI API Key")
        api_layout = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("sk-...")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        api_layout.addWidget(self.api_key_input)
        self.show_key_btn = QPushButton("👁")
        self.show_key_btn.setFixedWidth(40)
        self.show_key_btn.setStyleSheet("""
            QPushButton { background-color: #2d2d4a; border-radius: 6px; padding: 8px; }
            QPushButton:hover { background-color: #3d3d5a; }
        """)
        self.show_key_btn.clicked.connect(self._toggle_key_visibility)
        api_layout.addWidget(self.show_key_btn)
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # ── Shortcuts ──
        shortcut_group = QGroupBox("Shortcuts")
        shortcut_layout = QFormLayout()
        shortcut_layout.setSpacing(10)

        self.toggle_input = ShortcutCaptureInput()
        shortcut_layout.addRow("Toggle (start/stop):", self.toggle_input)

        self.ptt_input = ShortcutCaptureInput()
        shortcut_layout.addRow("Push-to-talk (hold):", self.ptt_input)

        shortcut_group.setLayout(shortcut_layout)
        layout.addWidget(shortcut_group)

        # ── Microphone ──
        mic_group = QGroupBox("Microphone")
        mic_layout = QFormLayout()
        mic_layout.setSpacing(10)

        self.mic_combo = QComboBox()
        self.mic_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        mic_layout.addRow("Input device:", self.mic_combo)

        self.mic_refresh_btn = QPushButton("🔄 Refresh")
        self.mic_refresh_btn.setFixedWidth(100)
        self.mic_refresh_btn.setStyleSheet("""
            QPushButton { background-color: #2d2d4a; border-radius: 6px; padding: 8px; }
            QPushButton:hover { background-color: #3d3d5a; }
        """)
        self.mic_refresh_btn.setCursor(Qt.PointingHandCursor)
        mic_layout.addRow(self.mic_refresh_btn)

        mic_group.setLayout(mic_layout)
        layout.addWidget(mic_group)

        # ── Options ──
        options_group = QGroupBox("Options")
        options_layout = QFormLayout()
        options_layout.setSpacing(10)

        self.language_input = QComboBox()
        self.language_input.setEditable(True)
        self.language_input.addItems([
            "", "en", "es", "fr", "de", "it", "pt", "nl", "ru", "zh",
            "ja", "ko", "ar", "hi", "pl", "uk", "cs", "sv", "da", "fi",
        ])
        self.language_input.setCurrentText("")
        self.language_input.lineEdit().setPlaceholderText("Auto-detect")
        options_layout.addRow("Language:", self.language_input)

        self.start_minimized_cb = QCheckBox("Start minimized to tray")
        options_layout.addRow(self.start_minimized_cb)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # ── Save Button ──
        self.save_btn = QPushButton("💾  Save Settings")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.save_btn)

        layout.addStretch()

        # ── Connect signals ──
        self.signals.audio_level.connect(self.audio_level.set_level)
        self.signals.status_update.connect(self._set_status)

    def _toggle_key_visibility(self) -> None:
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.show_key_btn.setText("🔒")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.show_key_btn.setText("👁")

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_recording_state(self, recording: bool) -> None:
        """Update UI to reflect recording state."""
        self.record_btn.recording = recording
        self.audio_level.recording = recording
        if recording:
            self.status_label.setProperty("recording", "true")
            self.status_label.setText("🔴 Recording... speak now")
            self.live_preview.setText("")
            self.live_preview.show()
        else:
            self.status_label.setProperty("recording", "false")
            self.live_preview.hide()
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def append_live_text(self, delta: str) -> None:
        """Append incremental transcription text to the live preview."""
        current = self.live_preview.text()
        self.live_preview.setText(current + delta)

    def reset_live_text(self) -> None:
        """Clear the live preview for a new speech turn."""
        self.live_preview.setText("")

    def populate_mic_list(self, devices: list[dict], saved_device: str = "") -> None:
        """Fill the microphone combo box with available input devices.

        *devices* should come from :func:`audio.list_input_devices`.
        *saved_device* is the ``input_device`` value from the config (a
        string like ``"3"`` for device index 3, or ``""`` for default).
        """
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("System Default", userData="")
        for dev in devices:
            label = f"{dev['name']}  (#{dev['index']})"
            self.mic_combo.addItem(label, userData=str(dev["index"]))

        # Restore saved selection
        if saved_device:
            for i in range(self.mic_combo.count()):
                if self.mic_combo.itemData(i) == saved_device:
                    self.mic_combo.setCurrentIndex(i)
                    break
        self.mic_combo.blockSignals(False)

    def selected_device_index(self) -> str:
        """Return the device index string of the currently selected mic (\"\" = default)."""
        data = self.mic_combo.currentData()
        return data if data else ""

    def load_config(self, config) -> None:
        """Populate UI fields from config object."""
        self.api_key_input.setText(config.openai_api_key)
        self.toggle_input.set_shortcut_string(config.toggle_shortcut)
        self.ptt_input.set_shortcut_string(config.ptt_shortcut)
        self.language_input.setCurrentText(config.language)
        self.start_minimized_cb.setChecked(config.start_minimized)

    def save_to_config(self, config) -> None:
        """Write UI field values back to config object."""
        config.openai_api_key = self.api_key_input.text().strip()
        config.toggle_shortcut = self.toggle_input.shortcut_string()
        config.ptt_shortcut = self.ptt_input.shortcut_string()
        config.language = self.language_input.currentText().strip()
        config.start_minimized = self.start_minimized_cb.isChecked()
        config.input_device = self.selected_device_index()

    def closeEvent(self, event) -> None:
        """Minimize to tray instead of quitting."""
        event.ignore()
        self.hide()


def create_tray_icon(app: QApplication, window: MainWindow) -> QSystemTrayIcon:
    """Create and configure the system tray icon."""
    tray = QSystemTrayIcon(svg_to_icon(TRAY_ICON_SVG), app)

    menu = QMenu()
    show_action = QAction("Show VoiceBoard", menu)
    show_action.triggered.connect(window.show)
    show_action.triggered.connect(window.raise_)
    menu.addAction(show_action)

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: (
        window.show() or window.raise_()
    ) if reason == QSystemTrayIcon.DoubleClick else None)
    tray.setToolTip("VoiceBoard — Voice Keyboard")
    tray.show()
    return tray
