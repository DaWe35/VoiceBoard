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
    QStackedWidget,
    QTextEdit,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import Qt, QSize, Signal, QObject, QTimer
from PySide6.QtGui import QIcon, QPixmap, QFont, QAction, QPainter, QColor, QPen, QKeySequence

from voiceboard.resources import TRAY_ICON_SVG, TRAY_ICON_RECORDING_SVG


_COPY_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
  viewBox="0 0 24 24" fill="none" stroke="{color}"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
</svg>"""

_CHECK_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
  viewBox="0 0 24 24" fill="none" stroke="{color}"
  stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="20 6 9 17 4 12"/>
</svg>"""


def _make_icon_from_svg(svg_template: str, size: int = 24, color: str = "#b0b0d0") -> QIcon:
    """Create a QIcon from an SVG template string with a {color} placeholder."""
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray

    svg = svg_template.replace("{color}", color)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


_REFRESH_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
  viewBox="0 0 24 24" fill="none" stroke="{color}"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 2v6h-6"/>
  <path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
  <path d="M3 22v-6h6"/>
  <path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
</svg>"""


def _make_refresh_icon(size: int = 24, color: str = "#b0b0d0") -> QIcon:
    """Create a refresh icon from an SVG template."""
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray

    svg = _REFRESH_ICON_SVG.replace("{color}", color)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


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
    font-family: 'Helvetica Neue', sans-serif;
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
            self.setText("STOP")
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
            self.setText("START")


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
      - Simultaneous combos: Ctrl+Shift+V, Space+B, A+S
      - Sequential combos (incl. double-tap): press one key/chord,
        release, press a second key/chord  → ``a,b`` or ``<ctrl>,<ctrl>``

    Click the field → it enters "listening" mode → press keys →
    they get recorded.  Press Escape to clear.

    Config format examples:
      ``<ctrl>+<shift>+v``   (simultaneous)
      ``<ctrl>,<ctrl>``      (sequential / double-tap)
      ``a,b``                (sequential, two different keys)
    """

    shortcut_changed = Signal(str)  # emits the config-format string
    capture_started = Signal()       # emitted when the field starts listening
    capture_ended = Signal()         # emitted when the field stops listening

    # How long (ms) to wait after all keys are released before treating
    # the chord as complete.  Gives the user time to press extra keys.
    _RELEASE_GRACE_MS = 80

    # How long (ms) to wait for the second chord in a sequential combo.
    _SEQ_WINDOW_MS = 800

    # Qt key codes that are modifier-only
    _MODIFIER_KEYS = {
        Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_AltGr,
        Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R,
    }

    # Qt key → (display name, config token)
    _KEY_NAMES: dict[int, tuple[str, str]] = {
        # Modifiers
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

        # ── Chord capture state ──
        self._held_keys: set[int] = set()   # keys currently physically held
        self._chord_keys: list[int] = []    # all keys seen in the current chord
        self._pressing = False              # True while at least one key is held

        # Short grace timer — after ALL keys are released, wait a tiny bit
        # in case the user is still rolling off a chord.
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(self._on_chord_complete)

        # ── Sequential capture state ──
        self._first_chord: Optional[list[int]] = None  # keys from the first chord
        self._waiting_for_second = False
        self._seq_timer = QTimer(self)
        self._seq_timer.setSingleShot(True)
        self._seq_timer.timeout.connect(self._on_seq_timeout)

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

    def _reset_capture_state(self) -> None:
        self._held_keys.clear()
        self._chord_keys.clear()
        self._pressing = False
        self._release_timer.stop()
        self._first_chord = None
        self._waiting_for_second = False
        self._seq_timer.stop()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._listening = True
        self._reset_capture_state()
        self.setText("Press a key combination…")
        self._update_style()
        self.capture_started.emit()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._listening = False
        self._reset_capture_state()
        if self._shortcut_str:
            self.setText(self._shortcut_to_display(self._shortcut_str))
        else:
            self.setText("")
        self._update_style()
        self.capture_ended.emit()

    def _key_info(self, qt_key: int, event=None) -> tuple[str, str] | None:
        """Return (display_name, config_token) for a Qt key code."""
        if qt_key in self._KEY_NAMES:
            return self._KEY_NAMES[qt_key]

        # Direct mapping for A-Z and 0-9 so we never depend on event.text()
        if Qt.Key_A <= qt_key <= Qt.Key_Z:
            ch = chr(qt_key).lower()
            return (ch.upper(), ch)
        if Qt.Key_0 <= qt_key <= Qt.Key_9:
            ch = chr(qt_key)
            return (ch, ch)

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
            self._reset_capture_state()
            self.setText("")
            self.shortcut_changed.emit("")
            self.clearFocus()
            return

        # Ignore auto-repeat
        if event.isAutoRepeat():
            return

        # Stop the release grace timer — another key is being pressed
        self._release_timer.stop()

        self._pressing = True
        self._held_keys.add(key)
        if key not in self._chord_keys:
            self._chord_keys.append(key)

        # Show live preview of keys being held
        self._show_held_preview()

    def keyReleaseEvent(self, event) -> None:
        if not self._listening:
            return
        if event.isAutoRepeat():
            return

        key = event.key()
        self._held_keys.discard(key)

        if not self._held_keys and self._pressing:
            # All keys released — start the grace timer
            self._pressing = False
            self._release_timer.start(self._RELEASE_GRACE_MS)

    def _show_held_preview(self) -> None:
        """Show a live preview of the keys currently being pressed."""
        parts = self._keys_display(self._chord_keys)
        prefix = ""
        if self._first_chord is not None:
            first_parts = self._keys_display(self._first_chord)
            prefix = " + ".join(first_parts) + " , "
        if parts:
            self.setText(prefix + " + ".join(parts) + " …")

    def _on_chord_complete(self) -> None:
        """Called after the grace period when a chord is fully released."""
        keys = list(self._chord_keys)
        self._chord_keys.clear()

        if not keys:
            return

        if self._waiting_for_second:
            # This is the SECOND chord → commit as sequential
            self._seq_timer.stop()
            self._waiting_for_second = False
            first = self._first_chord or []
            self._first_chord = None
            self._commit_sequential(first, keys)
            return

        # First chord received — wait to see if a second chord follows
        self._first_chord = keys
        self._waiting_for_second = True

        first_display = self._keys_display(keys)
        self.setText(" + ".join(first_display) + "  (press another key for sequence, or wait…)")
        self._seq_timer.start(self._SEQ_WINDOW_MS)

    def _on_seq_timeout(self) -> None:
        """Sequential window expired — commit the first chord as a regular combo."""
        self._waiting_for_second = False
        first = self._first_chord
        self._first_chord = None
        if first:
            self._commit_combo(first)

    def _commit_sequential(self, first_keys: list[int], second_keys: list[int]) -> None:
        """Commit a sequential shortcut (two chords separated by comma)."""
        first_parts = self._keys_to_parts(first_keys)
        second_parts = self._keys_to_parts(second_keys)
        if not first_parts or not second_parts:
            return

        first_tokens = "+".join(t for _, t in first_parts)
        second_tokens = "+".join(t for _, t in second_parts)
        config_text = f"{first_tokens},{second_tokens}"

        first_display = " + ".join(d for d, _ in first_parts)
        second_display = " + ".join(d for d, _ in second_parts)
        display_text = f"{first_display} , {second_display}"

        self._shortcut_str = config_text
        self.setText(display_text)
        self.shortcut_changed.emit(config_text)
        self.clearFocus()

    def _commit_combo(self, keys: list[int]) -> None:
        """Build the config string from a list of Qt key codes and commit."""
        parts = self._keys_to_parts(keys)
        if not parts:
            return

        display_text = " + ".join(d for d, _ in parts)
        config_text = "+".join(t for _, t in parts)

        self._shortcut_str = config_text
        self.setText(display_text)
        self.shortcut_changed.emit(config_text)
        self.clearFocus()

    def _keys_to_parts(self, keys: list[int]) -> list[tuple[str, str]]:
        """Return [(display, token), ...] sorted with modifiers first."""
        mods = []
        rest = []
        for k in keys:
            info = self._key_info(k)
            if info:
                if k in self._MODIFIER_KEYS:
                    mods.append(info)
                else:
                    rest.append(info)
        return mods + rest

    def _keys_display(self, keys: list[int]) -> list[str]:
        """Return display names for a list of keys."""
        result = []
        for k in keys:
            info = self._key_info(k)
            if info:
                result.append(info[0])
        return result

    @staticmethod
    def _shortcut_to_display(shortcut_str: str) -> str:
        """Convert a config-format shortcut string to a nice display label."""
        if not shortcut_str:
            return ""

        # Handle legacy "2x<token>" format
        if shortcut_str.startswith("2x"):
            inner = shortcut_str[2:]
            inner_disp = ShortcutCaptureInput._token_to_display(inner)
            return f"{inner_disp} , {inner_disp}"

        # Sequential combo — "a,b" or "<ctrl>,<ctrl>"
        if "," in shortcut_str:
            halves = shortcut_str.split(",", 1)
            left = ShortcutCaptureInput._combo_to_display(halves[0])
            right = ShortcutCaptureInput._combo_to_display(halves[1])
            return f"{left} , {right}"

        return ShortcutCaptureInput._combo_to_display(shortcut_str)

    @staticmethod
    def _combo_to_display(combo_str: str) -> str:
        """Convert a simultaneous combo portion to display text."""
        parts = combo_str.split("+")
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
    transcription_text = Signal(str, int)  # (text, backspace_count)
    transcription_error = Signal(str)
    audio_level = Signal(float)
    status_update = Signal(str)


class SettingsPage(QWidget):
    """Settings page containing all configuration controls."""

    back_requested = Signal()  # emitted when the user wants to go back
    opened = Signal()          # emitted when the page becomes visible
    closed = Signal()          # emitted when the page is hidden

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setStyleSheet("""
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 4px 0;
            }
            QScrollBar::handle:vertical {
                background: #3d3d5a;
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #6C63FF;
            }
            QScrollBar::handle:vertical:pressed {
                background: #5A52E0;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        page_layout.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        scroll.setWidget(content)

        # ── Header with back button ──
        header_row = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedWidth(80)
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setStyleSheet("""
            QPushButton { background-color: #2d2d4a; border-radius: 6px; padding: 8px; font-size: 13px; }
            QPushButton:hover { background-color: #3d3d5a; }
        """)
        self.back_btn.clicked.connect(self.back_requested.emit)
        header_row.addWidget(self.back_btn)

        header = QLabel("Settings")
        hfont = QFont()
        hfont.setPointSize(18)
        hfont.setWeight(QFont.Bold)
        header.setFont(hfont)
        header.setStyleSheet("color: #6C63FF;")
        header.setAlignment(Qt.AlignCenter)
        header_row.addWidget(header, 1)

        # Spacer to balance the back button
        spacer = QWidget()
        spacer.setFixedWidth(80)
        header_row.addWidget(spacer)

        layout.addLayout(header_row)

        # ── API Key ──
        api_group = QGroupBox("Soniox API Key")
        api_layout = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your Soniox API key...")
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

        self._toggle_warn = QLabel()
        self._toggle_warn.setWordWrap(True)
        self._toggle_warn.setTextFormat(Qt.RichText)
        self._toggle_warn.hide()
        shortcut_layout.addRow("", self._toggle_warn)

        self.ptt_input = ShortcutCaptureInput()
        shortcut_layout.addRow("Push-to-talk (hold):", self.ptt_input)

        self._ptt_warn = QLabel()
        self._ptt_warn.setWordWrap(True)
        self._ptt_warn.setTextFormat(Qt.RichText)
        self._ptt_warn.hide()
        shortcut_layout.addRow("", self._ptt_warn)

        # Style for warning labels
        _warn_style = (
            "QLabel { color: #FFD580; font-size: 11px; "
            "background-color: #2a2210; border: 1px solid #665520; "
            "border-radius: 4px; padding: 4px 8px; }"
        )
        self._toggle_warn.setStyleSheet(_warn_style)
        self._ptt_warn.setStyleSheet(_warn_style)

        # Update warnings when shortcuts change
        self.toggle_input.shortcut_changed.connect(
            lambda s: self._update_shortcut_warning(s, self._toggle_warn))
        self.ptt_input.shortcut_changed.connect(
            lambda s: self._update_shortcut_warning(s, self._ptt_warn))

        shortcut_group.setLayout(shortcut_layout)
        layout.addWidget(shortcut_group)

        # ── Microphone ──
        mic_group = QGroupBox("Microphone")
        mic_layout = QVBoxLayout()
        mic_layout.setSpacing(10)

        form = QFormLayout()
        mic_row = QHBoxLayout()
        self.mic_combo = QComboBox()
        self.mic_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        mic_row.addWidget(self.mic_combo)

        self.mic_refresh_btn = QPushButton()
        self.mic_refresh_btn.setIcon(_make_refresh_icon(24, "#b0b0d0"))
        self.mic_refresh_btn.setIconSize(QSize(20, 20))
        self.mic_refresh_btn.setFixedSize(36, 36)
        self.mic_refresh_btn.setToolTip("Refresh device list")
        self.mic_refresh_btn.setStyleSheet("""
            QPushButton { background-color: #2d2d4a; border-radius: 6px; padding: 4px; }
            QPushButton:hover { background-color: #3d3d5a; }
        """)
        self.mic_refresh_btn.setCursor(Qt.PointingHandCursor)
        mic_row.addWidget(self.mic_refresh_btn)

        form.addRow("Input device:", mic_row)
        mic_layout.addLayout(form)

        # Audio level preview
        level_label = QLabel("Level preview:")
        level_label.setStyleSheet("color: #7070a0; font-size: 12px; margin-top: 4px;")
        mic_layout.addWidget(level_label)
        self.audio_level = AudioLevelWidget()
        mic_layout.addWidget(self.audio_level)

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

        self.auto_start_cb = QCheckBox("Launch on system startup")
        options_layout.addRow(self.auto_start_cb)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        layout.addStretch()

    def _toggle_key_visibility(self) -> None:
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.show_key_btn.setText("🔒")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.show_key_btn.setText("👁")

    def populate_mic_list(self, devices: list[dict], saved_device: str = "") -> None:
        """Fill the microphone combo box with available input devices."""
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

    def _update_shortcut_warning(self, shortcut_str: str, label: QLabel) -> None:
        """Show/hide a Wayland-specific warning for *shortcut_str*."""
        from voiceboard.hotkeys import needs_evdev, is_wayland_without_evdev

        if shortcut_str and needs_evdev(shortcut_str) and is_wayland_without_evdev():
            label.setText(
                "<b>⚠ This shortcut likely won't work.</b><br>"
                "Because you're on Wayland, you can either use a modifier-based combo (e.g. <b>Ctrl+Shift+V</b>) "
                "or grant evdev access: "
                "<i>sudo usermod -aG input $USER</i> then re-login."
            )
            label.show()
        else:
            label.hide()

    def load_config(self, config) -> None:
        """Populate settings fields from config object."""
        self.api_key_input.setText(config.soniox_api_key)
        self.toggle_input.set_shortcut_string(config.toggle_shortcut)
        self.ptt_input.set_shortcut_string(config.ptt_shortcut)
        self.language_input.setCurrentText(config.language)
        self.auto_start_cb.setChecked(config.auto_start)

        # Show warnings if needed for loaded shortcuts
        self._update_shortcut_warning(config.toggle_shortcut, self._toggle_warn)
        self._update_shortcut_warning(config.ptt_shortcut, self._ptt_warn)

    def save_to_config(self, config) -> None:
        """Write settings field values back to config object."""
        config.soniox_api_key = self.api_key_input.text().strip()
        config.toggle_shortcut = self.toggle_input.shortcut_string()
        config.ptt_shortcut = self.ptt_input.shortcut_string()
        config.language = self.language_input.currentText().strip()
        config.auto_start = self.auto_start_cb.isChecked()
        config.input_device = self.selected_device_index()


class MainWindow(QMainWindow):
    """Main application window with a main page and a settings page."""

    def __init__(self):
        super().__init__()
        self.signals = SignalBridge()
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("VoiceBoard")
        self.setMinimumSize(320, 450)
        self.resize(self.minimumSize())
        self.setMaximumWidth(500)
        self.setWindowIcon(svg_to_icon(TRAY_ICON_SVG))

        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # ── Stacked widget for page switching ──
        self._stack = QStackedWidget()
        outer_layout.addWidget(self._stack)

        # ── Page 0: Main page ──
        main_page = QWidget()
        layout = QVBoxLayout(main_page)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Header ──
        header = QLabel("VoiceBoard")
        header.setAlignment(Qt.AlignCenter)
        hfont = QFont()
        hfont.setPointSize(22)
        hfont.setWeight(QFont.Bold)
        header.setFont(hfont)
        header.setStyleSheet("color: #6C63FF; margin-bottom: 4px;")
        layout.addWidget(header)

        subtitle = QLabel("Voice-to-text keyboard powered by Soniox")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #7070a0; font-size: 12px; margin-bottom: 8px;")
        layout.addWidget(subtitle)

        # ── Warning banner (hidden by default) ──
        self.warning_banner = QLabel()
        self.warning_banner.setWordWrap(True)
        self.warning_banner.setOpenExternalLinks(False)
        self.warning_banner.setTextFormat(Qt.RichText)
        self.warning_banner.setAlignment(Qt.AlignCenter)
        self.warning_banner.setStyleSheet(
            "QLabel {"
            "  background-color: #3a2a10;"
            "  color: #FFD580;"
            "  border: 1px solid #665520;"
            "  border-radius: 8px;"
            "  padding: 10px 14px;"
            "  font-size: 12px;"
            "}"
            "QLabel a { color: #FFB347; text-decoration: underline; }"
        )
        self.warning_banner.hide()
        layout.addWidget(self.warning_banner)

        # ── Record Button ──
        btn_container = QHBoxLayout()
        btn_container.setAlignment(Qt.AlignCenter)
        self.record_btn = RecordButton()
        btn_container.addWidget(self.record_btn)
        layout.addLayout(btn_container)

        # ── Status ──
        self.status_label = QLabel("Ready — press Start or use a shortcut")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # ── Live Transcription Preview ──
        self._preview_container = QWidget()
        self._preview_container.setObjectName("previewContainer")
        preview_layout = QVBoxLayout(self._preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        self.live_preview = QTextEdit()
        self.live_preview.setObjectName("livePreview")
        self.live_preview.setReadOnly(True)
        self.live_preview.setMinimumHeight(60)
        self.live_preview.setMaximumHeight(120)
        self.live_preview.setStyleSheet(
            "QTextEdit { color: #b0b0d0; font-size: 15px; font-style: italic; "
            "padding: 8px 28px 8px 8px; background-color: #16213e; border-radius: 8px; border: none; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #2d2d4a; border-radius: 3px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        preview_layout.addWidget(self.live_preview)

        # Small copy icon button overlaid inside the text area (top-right)
        self.copy_btn = QPushButton(self.live_preview)
        self.copy_btn.setFixedSize(26, 26)
        self.copy_btn.setIconSize(QSize(16, 16))
        self.copy_btn.setIcon(_make_icon_from_svg(_COPY_ICON_SVG, 16, "#b0b0d0"))
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setToolTip("Copy all session text")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(45, 45, 74, 0.85);
                border-radius: 5px;
                border: none;
                padding: 0px;
            }
            QPushButton:hover { background-color: rgba(61, 61, 90, 0.95); }
        """)
        self.copy_btn.clicked.connect(self._copy_session_text)
        # Reposition the button whenever the text area resizes
        self.live_preview.installEventFilter(self)

        self._preview_container.hide()
        layout.addWidget(self._preview_container)

        # Session text accumulator — stores ALL text from the session
        self._session_text = ""

        layout.addStretch()

        # ── Bottom buttons row ──
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d4a;
                border-radius: 8px;
                padding: 12px 20px;
                font-size: 14px;
                font-weight: bold;
                color: #b0b0d0;
            }
            QPushButton:hover { background-color: #3d3d5a; color: #e0e0e0; }
        """)
        self.settings_btn.clicked.connect(self._show_settings)
        bottom_row.addWidget(self.settings_btn)

        self.quit_btn = QPushButton("Quit")
        self.quit_btn.setCursor(Qt.PointingHandCursor)
        self.quit_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d4a;
                border-radius: 8px;
                padding: 12px 20px;
                font-size: 14px;
                font-weight: bold;
                color: #b0b0d0;
            }
            QPushButton:hover { background-color: #4a2030; color: #FF6B6B; }
        """)
        self.quit_btn.clicked.connect(QApplication.instance().quit)
        bottom_row.addWidget(self.quit_btn)

        layout.addLayout(bottom_row)

        self._stack.addWidget(main_page)  # index 0

        # ── Page 1: Settings page ──
        self.settings_page = SettingsPage()
        self.settings_page.back_requested.connect(self._show_main)
        self._stack.addWidget(self.settings_page)  # index 1

        # ── Expose settings widgets for backward compatibility ──
        self.api_key_input = self.settings_page.api_key_input
        self.toggle_input = self.settings_page.toggle_input
        self.ptt_input = self.settings_page.ptt_input
        self.language_input = self.settings_page.language_input
        self.auto_start_cb = self.settings_page.auto_start_cb
        self.mic_combo = self.settings_page.mic_combo
        self.mic_refresh_btn = self.settings_page.mic_refresh_btn
        self.audio_level = self.settings_page.audio_level

        # ── Connect signals ──
        self.signals.audio_level.connect(self.audio_level.set_level)
        self.signals.status_update.connect(self._set_status)

    def _show_settings(self) -> None:
        """Switch to the settings page."""
        self.setMinimumSize(420, 700)
        self._stack.setCurrentIndex(1)
        self.settings_page.opened.emit()

    def _show_main(self) -> None:
        """Switch back to the main page."""
        self._stack.setCurrentIndex(0)
        self.setMinimumSize(320, 450)
        self.resize(self.minimumSize())
        self.settings_page.closed.emit()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def show_warning(self, html: str) -> None:
        """Show a warning banner on the main page with the given rich-text."""
        self.warning_banner.setText(html)
        self.warning_banner.show()

    def hide_warning(self) -> None:
        """Hide the warning banner."""
        self.warning_banner.hide()

    def set_recording_state(self, recording: bool) -> None:
        """Update UI to reflect recording state."""
        self.record_btn.recording = recording
        if recording:
            self.status_label.setProperty("recording", "true")
            self.status_label.setText("🔴 Recording... speak now")
            # Reset session text and preview when starting a new session
            self._session_text = ""
            self.live_preview.clear()
            self._preview_container.show()
            # Switch to main page so the user sees the recording state
            self._show_main()
        else:
            self.status_label.setProperty("recording", "false")
            # Keep the preview container visible so the user can still
            # see and copy the text from the session that just ended.
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def update_live_text(self, text: str, backspace_count: int) -> None:
        """Update the live preview — erase *backspace_count* chars then append *text*.

        Also maintains ``_session_text`` which accumulates all text from
        the current session for the copy button.
        """
        current = self.live_preview.toPlainText()
        if backspace_count > 0:
            current = current[:-backspace_count] if backspace_count < len(current) else ""
        new_content = current + text
        self.live_preview.setPlainText(new_content)

        # Auto-scroll to the bottom so the latest words are always visible
        scrollbar = self.live_preview.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        # Update the session accumulator: apply the same backspace logic
        if backspace_count > 0:
            self._session_text = (
                self._session_text[:-backspace_count]
                if backspace_count < len(self._session_text)
                else ""
            )
        self._session_text += text

    def eventFilter(self, obj, event) -> bool:
        """Reposition the copy button when the preview text area is resized."""
        from PySide6.QtCore import QEvent
        if obj is self.live_preview and event.type() == QEvent.Resize:
            self.copy_btn.move(self.live_preview.width() - 30, 4)
        return super().eventFilter(obj, event)

    def _copy_session_text(self) -> None:
        """Copy all accumulated session text to the clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self._session_text)
        # Brief visual feedback — swap to a checkmark icon
        self.copy_btn.setIcon(_make_icon_from_svg(_CHECK_ICON_SVG, 16, "#6C63FF"))
        QTimer.singleShot(1500, lambda: self.copy_btn.setIcon(
            _make_icon_from_svg(_COPY_ICON_SVG, 16, "#b0b0d0")
        ))

    def populate_mic_list(self, devices: list[dict], saved_device: str = "") -> None:
        """Fill the microphone combo box with available input devices."""
        self.settings_page.populate_mic_list(devices, saved_device)

    def selected_device_index(self) -> str:
        """Return the device index string of the currently selected mic."""
        return self.settings_page.selected_device_index()

    def load_config(self, config) -> None:
        """Populate UI fields from config object."""
        self.settings_page.load_config(config)

    def save_to_config(self, config) -> None:
        """Write UI field values back to config object."""
        self.settings_page.save_to_config(config)

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

    def _on_tray_activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if window.isVisible() and not window.isMinimized():
                window.hide()
            else:
                window.showNormal()
                window.raise_()
                window.activateWindow()

    tray.activated.connect(_on_tray_activated)
    tray.setToolTip("VoiceBoard — Voice Keyboard")
    tray.show()
    return tray
