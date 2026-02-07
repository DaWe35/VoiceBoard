"""Global hotkey listener for VoiceBoard."""

import threading
from typing import Callable, Optional
from pynput import keyboard


class HotkeyManager:
    """Manages global hotkeys for toggle and push-to-talk modes."""

    def __init__(self):
        self._listener: Optional[keyboard.Listener] = None
        self._toggle_combo: set[keyboard.Key | keyboard.KeyCode] = set()
        self._ptt_combo: set[keyboard.Key | keyboard.KeyCode] = set()
        self._current_keys: set[keyboard.Key | keyboard.KeyCode] = set()

        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_press: Optional[Callable[[], None]] = None
        self.on_ptt_release: Optional[Callable[[], None]] = None

        self._toggle_active = False
        self._ptt_active = False
        self._lock = threading.Lock()

    def parse_shortcut(self, shortcut_str: str) -> set:
        """Parse a shortcut string like '<ctrl>+<shift>+v' into key set."""
        keys = set()
        if not shortcut_str:
            return keys

        parts = shortcut_str.lower().split("+")
        key_map = {
            "<ctrl>": keyboard.Key.ctrl_l,
            "<shift>": keyboard.Key.shift_l,
            "<alt>": keyboard.Key.alt_l,
            "<cmd>": keyboard.Key.cmd,
            "<super>": keyboard.Key.cmd,
        }

        for part in parts:
            part = part.strip()
            if part in key_map:
                keys.add(key_map[part])
            elif len(part) == 1:
                keys.add(keyboard.KeyCode.from_char(part))
            elif part.startswith("<") and part.endswith(">"):
                # Try to resolve as Key enum
                key_name = part[1:-1]
                try:
                    keys.add(keyboard.Key[key_name])
                except KeyError:
                    pass
        return keys

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        """Update the hotkey combinations."""
        self._toggle_combo = self.parse_shortcut(toggle_shortcut)
        self._ptt_combo = self.parse_shortcut(ptt_shortcut)

    def start(self) -> None:
        """Start listening for global hotkeys."""
        if self._listener is not None:
            self.stop()

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop listening for global hotkeys."""
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._current_keys.clear()

    def _normalize_key(self, key) -> keyboard.Key | keyboard.KeyCode:
        """Normalize key variants (e.g., ctrl_r -> ctrl_l)."""
        normalization = {
            keyboard.Key.ctrl_r: keyboard.Key.ctrl_l,
            keyboard.Key.shift_r: keyboard.Key.shift_l,
            keyboard.Key.alt_r: keyboard.Key.alt_l,
            keyboard.Key.alt_gr: keyboard.Key.alt_l,
        }
        return normalization.get(key, key)

    def _on_press(self, key) -> None:
        normalized = self._normalize_key(key)
        self._current_keys.add(normalized)

        # Check toggle shortcut
        if self._toggle_combo and self._toggle_combo.issubset(self._current_keys):
            with self._lock:
                if not self._toggle_active:
                    self._toggle_active = True
                    if self.on_toggle:
                        self.on_toggle()

        # Check PTT shortcut (press)
        if self._ptt_combo and self._ptt_combo.issubset(self._current_keys):
            with self._lock:
                if not self._ptt_active:
                    self._ptt_active = True
                    if self.on_ptt_press:
                        self.on_ptt_press()

    def _on_release(self, key) -> None:
        normalized = self._normalize_key(key)

        # Check PTT release before removing key
        if self._ptt_active and self._ptt_combo:
            if normalized in self._ptt_combo:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # Reset toggle active flag when any toggle key is released
        if self._toggle_active and self._toggle_combo:
            if normalized in self._toggle_combo:
                with self._lock:
                    self._toggle_active = False

        self._current_keys.discard(normalized)
