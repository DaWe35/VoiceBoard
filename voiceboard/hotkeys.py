"""Global hotkey listener for VoiceBoard.

Platform strategies:
  - Linux → evdev (works on both X11 and Wayland)
  - macOS / Windows → pynput keyboard Listener
"""

import logging
import os
import platform
import select
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

_SYSTEM = platform.system()


# ── Shortcut string parsing helpers ────────────────────────────

def _parse_shortcut_evdev(shortcut_str: str) -> set[int]:
    """Parse a pynput-format shortcut string into a set of evdev key codes."""
    from evdev import ecodes

    keys: set[int] = set()
    if not shortcut_str:
        return keys

    _TOKEN_MAP: dict[str, list[int]] = {
        "<ctrl>": [ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL],
        "<shift>": [ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT],
        "<alt>": [ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT],
        "<super>": [ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA],
        "<cmd>": [ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA],
        "<space>": [ecodes.KEY_SPACE],
        "<enter>": [ecodes.KEY_ENTER],
        "<tab>": [ecodes.KEY_TAB],
        "<backspace>": [ecodes.KEY_BACKSPACE],
        "<delete>": [ecodes.KEY_DELETE],
        "<home>": [ecodes.KEY_HOME],
        "<end>": [ecodes.KEY_END],
        "<page_up>": [ecodes.KEY_PAGEUP],
        "<page_down>": [ecodes.KEY_PAGEDOWN],
        "<up>": [ecodes.KEY_UP],
        "<down>": [ecodes.KEY_DOWN],
        "<left>": [ecodes.KEY_LEFT],
        "<right>": [ecodes.KEY_RIGHT],
        "<insert>": [ecodes.KEY_INSERT],
        "<pause>": [ecodes.KEY_PAUSE],
        "<print_screen>": [ecodes.KEY_SYSRQ],
        "<scroll_lock>": [ecodes.KEY_SCROLLLOCK],
        "<caps_lock>": [ecodes.KEY_CAPSLOCK],
        "<num_lock>": [ecodes.KEY_NUMLOCK],
    }
    for i in range(1, 13):
        _TOKEN_MAP[f"<f{i}>"] = [getattr(ecodes, f"KEY_F{i}")]

    # Single-char → evdev key code mapping
    _CHAR_MAP: dict[str, int] = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        _CHAR_MAP[c] = getattr(ecodes, f"KEY_{c.upper()}")
    for d in "0123456789":
        _CHAR_MAP[d] = getattr(ecodes, f"KEY_{d}")
    _CHAR_MAP["-"] = ecodes.KEY_MINUS
    _CHAR_MAP["="] = ecodes.KEY_EQUAL
    _CHAR_MAP["["] = ecodes.KEY_LEFTBRACE
    _CHAR_MAP["]"] = ecodes.KEY_RIGHTBRACE
    _CHAR_MAP[";"] = ecodes.KEY_SEMICOLON
    _CHAR_MAP["'"] = ecodes.KEY_APOSTROPHE
    _CHAR_MAP[","] = ecodes.KEY_COMMA
    _CHAR_MAP["."] = ecodes.KEY_DOT
    _CHAR_MAP["/"] = ecodes.KEY_SLASH
    _CHAR_MAP["\\"] = ecodes.KEY_BACKSLASH
    _CHAR_MAP["`"] = ecodes.KEY_GRAVE

    parts = shortcut_str.split("+")
    for part in parts:
        part = part.strip().lower()
        if part in _TOKEN_MAP:
            # For modifiers, store the left variant as the canonical one
            keys.add(_TOKEN_MAP[part][0])
        elif part in _CHAR_MAP:
            keys.add(_CHAR_MAP[part])
        elif part.startswith("<") and part.endswith(">"):
            # Try KEY_<name> directly
            key_name = part[1:-1].upper()
            code = getattr(ecodes, f"KEY_{key_name}", None)
            if code is not None:
                keys.add(code)
            else:
                log.warning("Unknown key token in shortcut: %s", part)
        else:
            log.warning("Unknown shortcut part: %s", part)

    return keys


# Modifier evdev codes (left and right variants) for normalisation
_EVDEV_MODIFIER_PAIRS: dict[int, int] = {}


def _init_modifier_pairs():
    global _EVDEV_MODIFIER_PAIRS
    try:
        from evdev import ecodes
        _EVDEV_MODIFIER_PAIRS = {
            ecodes.KEY_RIGHTCTRL: ecodes.KEY_LEFTCTRL,
            ecodes.KEY_RIGHTSHIFT: ecodes.KEY_LEFTSHIFT,
            ecodes.KEY_RIGHTALT: ecodes.KEY_LEFTALT,
            ecodes.KEY_RIGHTMETA: ecodes.KEY_LEFTMETA,
        }
    except ImportError:
        pass


# ── Linux evdev backend ────────────────────────────────────────

class _EvdevHotkeyListener:
    """Listen for global hotkeys using evdev (works on X11 and Wayland)."""

    def __init__(self):
        self._toggle_combo: set[int] = set()
        self._ptt_combo: set[int] = set()
        self._current_keys: set[int] = set()

        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_press: Optional[Callable[[], None]] = None
        self.on_ptt_release: Optional[Callable[[], None]] = None

        self._toggle_active = False
        self._ptt_active = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stop_pipe_r: Optional[int] = None
        self._stop_pipe_w: Optional[int] = None

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        _init_modifier_pairs()
        self._toggle_combo = _parse_shortcut_evdev(toggle_shortcut)
        self._ptt_combo = _parse_shortcut_evdev(ptt_shortcut)
        log.debug("Toggle combo evdev codes: %s", self._toggle_combo)
        log.debug("PTT combo evdev codes: %s", self._ptt_combo)

    def start(self) -> None:
        if self._thread is not None:
            self.stop()

        self._stop_event.clear()
        self._stop_pipe_r, self._stop_pipe_w = os.pipe()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        # Write to pipe to wake up select()
        if self._stop_pipe_w is not None:
            try:
                os.write(self._stop_pipe_w, b"\x00")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        # Close pipe fds
        for fd in (self._stop_pipe_r, self._stop_pipe_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._stop_pipe_r = None
        self._stop_pipe_w = None
        self._current_keys.clear()

    def _normalize_key(self, code: int) -> int:
        return _EVDEV_MODIFIER_PAIRS.get(code, code)

    def _run(self) -> None:
        import evdev
        from evdev import ecodes

        # Find all keyboard devices
        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                # Check if device has EV_KEY capability with actual keyboard keys
                if ecodes.EV_KEY in caps:
                    key_caps = caps[ecodes.EV_KEY]
                    # Must have at least some letter keys to be a keyboard
                    if ecodes.KEY_A in key_caps and ecodes.KEY_Z in key_caps:
                        devices.append(dev)
                        log.debug("Monitoring keyboard: %s (%s)", dev.name, dev.path)
                    else:
                        dev.close()
                else:
                    dev.close()
            except (PermissionError, OSError) as e:
                log.debug("Cannot open %s: %s", path, e)

        if not devices:
            log.error(
                "No keyboard devices found! "
                "Make sure the user is in the 'input' group: "
                "sudo usermod -aG input $USER  (then re-login)"
            )
            return

        try:
            while not self._stop_event.is_set():
                # Use select to wait on all devices + the stop pipe
                fds = {dev.fd: dev for dev in devices}
                read_fds = list(fds.keys())
                if self._stop_pipe_r is not None:
                    read_fds.append(self._stop_pipe_r)

                readable, _, _ = select.select(read_fds, [], [], 1.0)

                for fd in readable:
                    if fd == self._stop_pipe_r:
                        return  # stop requested

                    dev = fds.get(fd)
                    if dev is None:
                        continue

                    try:
                        for event in dev.read():
                            if event.type != ecodes.EV_KEY:
                                continue

                            code = self._normalize_key(event.code)

                            if event.value == 1:  # key press
                                self._current_keys.add(code)
                                self._check_press()
                            elif event.value == 0:  # key release
                                self._check_release(code)
                                self._current_keys.discard(code)
                            # value == 2 is key repeat, ignore
                    except OSError:
                        log.debug("Device %s disconnected", dev.path)
                        devices.remove(dev)
        finally:
            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass

    def _check_press(self) -> None:
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

    def _check_release(self, code: int) -> None:
        # Check PTT release
        if self._ptt_active and self._ptt_combo:
            if code in self._ptt_combo:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # Reset toggle active flag
        if self._toggle_active and self._toggle_combo:
            if code in self._toggle_combo:
                with self._lock:
                    self._toggle_active = False


# ── pynput backend (macOS / Windows) ───────────────────────────

class _PynputHotkeyListener:
    """Listen for global hotkeys using pynput (macOS / Windows)."""

    def __init__(self):
        self._listener = None
        self._toggle_combo: set = set()
        self._ptt_combo: set = set()
        self._current_keys: set = set()

        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_press: Optional[Callable[[], None]] = None
        self.on_ptt_release: Optional[Callable[[], None]] = None

        self._toggle_active = False
        self._ptt_active = False
        self._lock = threading.Lock()

    def _parse_shortcut(self, shortcut_str: str) -> set:
        """Parse a shortcut string like '<ctrl>+<shift>+v' into pynput key set."""
        from pynput import keyboard

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
                key_name = part[1:-1]
                try:
                    keys.add(keyboard.Key[key_name])
                except KeyError:
                    pass
        return keys

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        self._toggle_combo = self._parse_shortcut(toggle_shortcut)
        self._ptt_combo = self._parse_shortcut(ptt_shortcut)

    def start(self) -> None:
        from pynput import keyboard

        if self._listener is not None:
            self.stop()

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._current_keys.clear()

    def _normalize_key(self, key):
        from pynput import keyboard

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

        if self._toggle_combo and self._toggle_combo.issubset(self._current_keys):
            with self._lock:
                if not self._toggle_active:
                    self._toggle_active = True
                    if self.on_toggle:
                        self.on_toggle()

        if self._ptt_combo and self._ptt_combo.issubset(self._current_keys):
            with self._lock:
                if not self._ptt_active:
                    self._ptt_active = True
                    if self.on_ptt_press:
                        self.on_ptt_press()

    def _on_release(self, key) -> None:
        normalized = self._normalize_key(key)

        if self._ptt_active and self._ptt_combo:
            if normalized in self._ptt_combo:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        if self._toggle_active and self._toggle_combo:
            if normalized in self._toggle_combo:
                with self._lock:
                    self._toggle_active = False

        self._current_keys.discard(normalized)


# ── Public facade ──────────────────────────────────────────────

class HotkeyManager:
    """Manages global hotkeys — auto-selects the best backend for the platform."""

    def __init__(self):
        if _SYSTEM == "Linux":
            self._backend = _EvdevHotkeyListener()
            log.info("Using evdev backend for global hotkeys")
        else:
            self._backend = _PynputHotkeyListener()
            log.info("Using pynput backend for global hotkeys")

    # ── Delegate everything to the backend ──

    @property
    def on_toggle(self):
        return self._backend.on_toggle

    @on_toggle.setter
    def on_toggle(self, cb):
        self._backend.on_toggle = cb

    @property
    def on_ptt_press(self):
        return self._backend.on_ptt_press

    @on_ptt_press.setter
    def on_ptt_press(self, cb):
        self._backend.on_ptt_press = cb

    @property
    def on_ptt_release(self):
        return self._backend.on_ptt_release

    @on_ptt_release.setter
    def on_ptt_release(self, cb):
        self._backend.on_ptt_release = cb

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        self._backend.set_shortcuts(toggle_shortcut, ptt_shortcut)

    def start(self) -> None:
        self._backend.start()

    def stop(self) -> None:
        self._backend.stop()
