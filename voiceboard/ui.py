"""Modern UI for VoiceBoard using PySide6 (Qt6)."""

import sys
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
            color = QColor("#6C63FF") if self._level < 0.7 else QColor("#FF6B6B")
            painter.setBrush(color)
            painter.drawRoundedRect(0, 0, w, self.height(), 4, 4)

        painter.end()


class ShortcutCaptureInput(QLineEdit):
    """A line-edit that captures key combinations when focused.

    Instead of typing text, the user presses a key combo (e.g. Ctrl+Shift+V)
    and the widget records it, displaying a human-readable label and storing
    the pynput-compatible shortcut string internally.

    Click the field → it enters "listening" mode → press a key combo →
    it gets recorded and the field shows the combo.  Press Escape or
    Backspace to clear.
    """

    shortcut_changed = Signal(str)  # emits the pynput-format string

    # Qt modifier flags → (display name, pynput token)
    _MODIFIER_MAP = [
        (Qt.ControlModifier, "Ctrl", "<ctrl>"),
        (Qt.ShiftModifier, "Shift", "<shift>"),
        (Qt.AltModifier, "Alt", "<alt>"),
        (Qt.MetaModifier, "Super", "<super>"),
    ]

    # Qt key codes for modifier-only keys (we ignore these as the "main" key)
    _MODIFIER_KEYS = {
        Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_AltGr,
        Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R,
    }

    # Mapping of special Qt keys → (display, pynput token)
    _SPECIAL_KEYS: dict[int, tuple[str, str]] = {
        Qt.Key_F1: ("F1", "<f1>"), Qt.Key_F2: ("F2", "<f2>"),
        Qt.Key_F3: ("F3", "<f3>"), Qt.Key_F4: ("F4", "<f4>"),
        Qt.Key_F5: ("F5", "<f5>"), Qt.Key_F6: ("F6", "<f6>"),
        Qt.Key_F7: ("F7", "<f7>"), Qt.Key_F8: ("F8", "<f8>"),
        Qt.Key_F9: ("F9", "<f9>"), Qt.Key_F10: ("F10", "<f10>"),
        Qt.Key_F11: ("F11", "<f11>"), Qt.Key_F12: ("F12", "<f12>"),
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
        self._shortcut_str = ""  # pynput-compatible string
        self._listening = False
        self._update_placeholder()

    def shortcut_string(self) -> str:
        """Return the stored pynput-format shortcut string."""
        return self._shortcut_str

    def set_shortcut_string(self, shortcut_str: str) -> None:
        """Set the shortcut from a pynput-format string and update display."""
        self._shortcut_str = shortcut_str
        if shortcut_str:
            self.setText(self._pynput_to_display(shortcut_str))
        else:
            self.setText("")
        self._listening = False
        self._update_style()

    def _update_placeholder(self) -> None:
        self.setPlaceholderText("Click here, then press a shortcut…")

    def _update_style(self) -> None:
        if self._listening:
            self.setStyleSheet(
                "QLineEdit { border: 2px solid #6C63FF; background-color: #1e1e3e; "
                "color: #6C63FF; font-weight: bold; }"
            )
        else:
            self.setStyleSheet("")  # revert to global stylesheet

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._listening = True
        self.setText("Press a key combination…")
        self._update_style()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._listening = False
        # Restore display text
        if self._shortcut_str:
            self.setText(self._pynput_to_display(self._shortcut_str))
        else:
            self.setText("")
        self._update_style()

    def keyPressEvent(self, event) -> None:
        if not self._listening:
            return

        key = event.key()
        modifiers = event.modifiers()

        # Escape → clear the shortcut
        if key == Qt.Key_Escape:
            self._shortcut_str = ""
            self.setText("")
            self.shortcut_changed.emit("")
            self.clearFocus()
            return

        # Ignore bare modifier presses — wait for the actual key
        if key in self._MODIFIER_KEYS:
            return

        # Build display and pynput strings
        display_parts = []
        pynput_parts = []

        for qt_mod, disp_name, pynput_token in self._MODIFIER_MAP:
            if modifiers & qt_mod:
                display_parts.append(disp_name)
                pynput_parts.append(pynput_token)

        # Resolve the main key
        if key in self._SPECIAL_KEYS:
            disp, pynput_tok = self._SPECIAL_KEYS[key]
            display_parts.append(disp)
            pynput_parts.append(pynput_tok)
        else:
            text = event.text()
            if text and text.isprintable():
                ch = text.lower()
                display_parts.append(ch.upper())
                pynput_parts.append(ch)
            else:
                # Unknown key — try to get a name from QKeySequence
                seq = QKeySequence(key)
                name = seq.toString()
                if name:
                    display_parts.append(name)
                    pynput_parts.append(f"<{name.lower()}>")
                else:
                    return  # unrecognised key, ignore

        display_text = " + ".join(display_parts)
        pynput_text = "+".join(pynput_parts)

        self._shortcut_str = pynput_text
        self.setText(display_text)
        self.shortcut_changed.emit(pynput_text)
        self.clearFocus()

    @staticmethod
    def _pynput_to_display(shortcut_str: str) -> str:
        """Convert a pynput-format shortcut string to a nice display label."""
        if not shortcut_str:
            return ""
        token_display = {
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
            token_display[f"<f{i}>"] = f"F{i}"

        parts = shortcut_str.split("+")
        display_parts = []
        for part in parts:
            part_stripped = part.strip()
            lower = part_stripped.lower()
            if lower in token_display:
                display_parts.append(token_display[lower])
            elif len(part_stripped) == 1:
                display_parts.append(part_stripped.upper())
            else:
                display_parts.append(part_stripped)
        return " + ".join(display_parts)


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
