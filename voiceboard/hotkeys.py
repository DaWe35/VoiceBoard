"""Global hotkey listener for VoiceBoard.

Platform strategies:
  - Linux → evdev (works on both X11 and Wayland)
  - macOS / Windows → pynput keyboard Listener

Shortcut format:
  - Regular combo: ``<ctrl>+<shift>+v``, ``<space>+b``
  - Double-tap:    ``2x<ctrl>``, ``2xb``
"""

import logging
import os
import platform
import select
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

_SYSTEM = platform.system()

# Double-tap detection window (seconds)
_DOUBLE_TAP_WINDOW = 0.4


# ── Shortcut config representation ────────────────────────────

class _ShortcutConfig:
    """Parsed shortcut — either a key combo or a double-tap."""

    def __init__(self):
        self.combo: set = set()        # set of key codes for a combo
        self.double_tap_key = None     # single key code for double-tap (or None)

    @property
    def is_double_tap(self) -> bool:
        return self.double_tap_key is not None

    @property
    def is_empty(self) -> bool:
        return not self.combo and self.double_tap_key is None


def _is_double_tap_str(shortcut_str: str) -> tuple[bool, str]:
    """Check if shortcut_str is a double-tap format.  Returns (is_dt, inner_token)."""
    if shortcut_str.startswith("2x"):
        return True, shortcut_str[2:]
    return False, shortcut_str


# ── Shortcut string parsing: evdev ─────────────────────────────

def _parse_shortcut_evdev(shortcut_str: str) -> _ShortcutConfig:
    """Parse a config-format shortcut string into an evdev _ShortcutConfig."""
    from evdev import ecodes

    cfg = _ShortcutConfig()
    if not shortcut_str:
        return cfg

    is_dt, inner = _is_double_tap_str(shortcut_str)

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

    def _resolve_token(token: str) -> list[int]:
        """Resolve a single token to evdev key code(s)."""
        token = token.strip().lower()
        if token in _TOKEN_MAP:
            return _TOKEN_MAP[token]
        if token in _CHAR_MAP:
            return [_CHAR_MAP[token]]
        if token.startswith("<") and token.endswith(">"):
            key_name = token[1:-1].upper()
            code = getattr(ecodes, f"KEY_{key_name}", None)
            if code is not None:
                return [code]
        log.warning("Unknown shortcut token: %s", token)
        return []

    if is_dt:
        # Double-tap: inner is a single token
        codes = _resolve_token(inner)
        if codes:
            cfg.double_tap_key = codes[0]
    else:
        # Regular combo
        parts = inner.split("+")
        for part in parts:
            codes = _resolve_token(part)
            if codes:
                cfg.combo.add(codes[0])  # use left variant as canonical

    return cfg


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
    """Listen for global hotkeys using evdev (works on X11 and Wayland).

    Supports regular key combos and double-tap shortcuts.
    """

    def __init__(self):
        self._toggle_cfg = _ShortcutConfig()
        self._ptt_cfg = _ShortcutConfig()
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

        # Double-tap state: {key_code: last_press_time}
        self._last_tap_time: dict[int, float] = {}

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        _init_modifier_pairs()
        self._toggle_cfg = _parse_shortcut_evdev(toggle_shortcut)
        self._ptt_cfg = _parse_shortcut_evdev(ptt_shortcut)
        log.debug("Toggle config: combo=%s dt_key=%s",
                  self._toggle_cfg.combo, self._toggle_cfg.double_tap_key)
        log.debug("PTT config: combo=%s dt_key=%s",
                  self._ptt_cfg.combo, self._ptt_cfg.double_tap_key)

    def start(self) -> None:
        if self._thread is not None:
            self.stop()

        self._stop_event.clear()
        self._stop_pipe_r, self._stop_pipe_w = os.pipe()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._stop_pipe_w is not None:
            try:
                os.write(self._stop_pipe_w, b"\x00")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        for fd in (self._stop_pipe_r, self._stop_pipe_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._stop_pipe_r = None
        self._stop_pipe_w = None
        self._current_keys.clear()
        self._last_tap_time.clear()

    def _normalize_key(self, code: int) -> int:
        return _EVDEV_MODIFIER_PAIRS.get(code, code)

    def _run(self) -> None:
        import evdev
        from evdev import ecodes

        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    key_caps = caps[ecodes.EV_KEY]
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
                fds = {dev.fd: dev for dev in devices}
                read_fds = list(fds.keys())
                if self._stop_pipe_r is not None:
                    read_fds.append(self._stop_pipe_r)

                readable, _, _ = select.select(read_fds, [], [], 1.0)

                for fd in readable:
                    if fd == self._stop_pipe_r:
                        return

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
                                self._check_press(code)
                            elif event.value == 0:  # key release
                                self._check_release(code)
                                self._current_keys.discard(code)
                    except OSError:
                        log.debug("Device %s disconnected", dev.path)
                        devices.remove(dev)
        finally:
            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass

    def _check_double_tap(self, cfg: _ShortcutConfig, code: int) -> bool:
        """Check if *code* is a double-tap for *cfg*.  Returns True if fired."""
        if not cfg.is_double_tap:
            return False
        if code != cfg.double_tap_key:
            return False

        now = time.monotonic()
        last = self._last_tap_time.get(code, 0.0)
        self._last_tap_time[code] = now

        if (now - last) <= _DOUBLE_TAP_WINDOW:
            # Reset so a third tap doesn't re-fire
            self._last_tap_time[code] = 0.0
            return True
        return False

    def _check_press(self, code: int) -> None:
        # Check double-tap toggle
        if self._toggle_cfg.is_double_tap:
            if self._check_double_tap(self._toggle_cfg, code):
                with self._lock:
                    if self.on_toggle:
                        self.on_toggle()
        # Check combo toggle
        elif (self._toggle_cfg.combo
              and self._toggle_cfg.combo.issubset(self._current_keys)):
            with self._lock:
                if not self._toggle_active:
                    self._toggle_active = True
                    if self.on_toggle:
                        self.on_toggle()

        # Check double-tap PTT
        if self._ptt_cfg.is_double_tap:
            if self._check_double_tap(self._ptt_cfg, code):
                with self._lock:
                    if not self._ptt_active:
                        self._ptt_active = True
                        if self.on_ptt_press:
                            self.on_ptt_press()
        # Check combo PTT
        elif (self._ptt_cfg.combo
              and self._ptt_cfg.combo.issubset(self._current_keys)):
            with self._lock:
                if not self._ptt_active:
                    self._ptt_active = True
                    if self.on_ptt_press:
                        self.on_ptt_press()

    def _check_release(self, code: int) -> None:
        # PTT release (combo mode)
        if self._ptt_active and self._ptt_cfg.combo:
            if code in self._ptt_cfg.combo:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # PTT release (double-tap mode — release the double-tapped key)
        if self._ptt_active and self._ptt_cfg.is_double_tap:
            if code == self._ptt_cfg.double_tap_key:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # Reset toggle active flag (combo mode)
        if self._toggle_active and self._toggle_cfg.combo:
            if code in self._toggle_cfg.combo:
                with self._lock:
                    self._toggle_active = False


# ── pynput backend (macOS / Windows) ───────────────────────────

class _PynputHotkeyListener:
    """Listen for global hotkeys using pynput (macOS / Windows).

    Supports regular key combos and double-tap shortcuts.
    """

    def __init__(self):
        self._listener = None
        self._toggle_cfg = _ShortcutConfig()
        self._ptt_cfg = _ShortcutConfig()
        self._current_keys: set = set()

        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_press: Optional[Callable[[], None]] = None
        self.on_ptt_release: Optional[Callable[[], None]] = None

        self._toggle_active = False
        self._ptt_active = False
        self._lock = threading.Lock()

        # Double-tap state
        self._last_tap_time: dict = {}  # key → monotonic time

    def _parse_shortcut(self, shortcut_str: str) -> _ShortcutConfig:
        """Parse a config-format shortcut string into a pynput _ShortcutConfig."""
        from pynput import keyboard

        cfg = _ShortcutConfig()
        if not shortcut_str:
            return cfg

        is_dt, inner = _is_double_tap_str(shortcut_str)

        key_map = {
            "<ctrl>": keyboard.Key.ctrl_l,
            "<shift>": keyboard.Key.shift_l,
            "<alt>": keyboard.Key.alt_l,
            "<cmd>": keyboard.Key.cmd,
            "<super>": keyboard.Key.cmd,
        }

        def _resolve_token(token: str):
            token = token.strip().lower()
            if token in key_map:
                return key_map[token]
            if len(token) == 1:
                return keyboard.KeyCode.from_char(token)
            if token.startswith("<") and token.endswith(">"):
                key_name = token[1:-1]
                try:
                    return keyboard.Key[key_name]
                except KeyError:
                    pass
            return None

        if is_dt:
            key = _resolve_token(inner)
            if key is not None:
                cfg.double_tap_key = key
        else:
            parts = inner.split("+")
            for part in parts:
                key = _resolve_token(part)
                if key is not None:
                    cfg.combo.add(key)

        return cfg

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        self._toggle_cfg = self._parse_shortcut(toggle_shortcut)
        self._ptt_cfg = self._parse_shortcut(ptt_shortcut)

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
        self._last_tap_time.clear()

    def _normalize_key(self, key):
        from pynput import keyboard

        normalization = {
            keyboard.Key.ctrl_r: keyboard.Key.ctrl_l,
            keyboard.Key.shift_r: keyboard.Key.shift_l,
            keyboard.Key.alt_r: keyboard.Key.alt_l,
            keyboard.Key.alt_gr: keyboard.Key.alt_l,
        }
        return normalization.get(key, key)

    def _check_double_tap(self, cfg: _ShortcutConfig, key) -> bool:
        if not cfg.is_double_tap:
            return False
        if key != cfg.double_tap_key:
            return False

        now = time.monotonic()
        last = self._last_tap_time.get(key, 0.0)
        self._last_tap_time[key] = now

        if (now - last) <= _DOUBLE_TAP_WINDOW:
            self._last_tap_time[key] = 0.0
            return True
        return False

    def _on_press(self, key) -> None:
        normalized = self._normalize_key(key)
        self._current_keys.add(normalized)

        # Toggle
        if self._toggle_cfg.is_double_tap:
            if self._check_double_tap(self._toggle_cfg, normalized):
                with self._lock:
                    if self.on_toggle:
                        self.on_toggle()
        elif (self._toggle_cfg.combo
              and self._toggle_cfg.combo.issubset(self._current_keys)):
            with self._lock:
                if not self._toggle_active:
                    self._toggle_active = True
                    if self.on_toggle:
                        self.on_toggle()

        # PTT
        if self._ptt_cfg.is_double_tap:
            if self._check_double_tap(self._ptt_cfg, normalized):
                with self._lock:
                    if not self._ptt_active:
                        self._ptt_active = True
                        if self.on_ptt_press:
                            self.on_ptt_press()
        elif (self._ptt_cfg.combo
              and self._ptt_cfg.combo.issubset(self._current_keys)):
            with self._lock:
                if not self._ptt_active:
                    self._ptt_active = True
                    if self.on_ptt_press:
                        self.on_ptt_press()

    def _on_release(self, key) -> None:
        normalized = self._normalize_key(key)

        # PTT release (combo)
        if self._ptt_active and self._ptt_cfg.combo:
            if normalized in self._ptt_cfg.combo:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # PTT release (double-tap)
        if self._ptt_active and self._ptt_cfg.is_double_tap:
            if normalized == self._ptt_cfg.double_tap_key:
                with self._lock:
                    self._ptt_active = False
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # Reset toggle active (combo)
        if self._toggle_active and self._toggle_cfg.combo:
            if normalized in self._toggle_cfg.combo:
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
