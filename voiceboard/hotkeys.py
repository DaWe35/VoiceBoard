"""Global hotkey listener for VoiceBoard.

Platform strategies:
  - Linux → evdev (works on both X11 and Wayland)
  - macOS / Windows → pynput keyboard Listener

Shortcut format:
  - Simultaneous combo:  ``<ctrl>+<shift>+v``, ``<space>+b``
  - Sequential combo:    ``<ctrl>,<ctrl>``  (double-tap Ctrl)
                         ``a,b``            (press A then B)
  - Legacy ``2x<ctrl>`` is accepted and converted to ``<ctrl>,<ctrl>``.
"""

import logging
import os
import platform
import select
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

_SYSTEM = platform.system()

# Time window (seconds) for the second press of a sequential shortcut.
_SEQ_WINDOW = 0.6


# ── Shortcut config ───────────────────────────────────────────

@dataclass
class _ShortcutConfig:
    """Parsed shortcut.

    *combo*     – frozenset of key codes that must ALL be held simultaneously.
    *seq_keys*  – tuple(first_key, second_key) for a sequential shortcut.

    Exactly one of them is non-empty for a valid shortcut.
    """
    combo: frozenset = field(default_factory=frozenset)
    seq_keys: tuple = ()          # (first_key_code, second_key_code)

    @property
    def is_sequential(self) -> bool:
        return len(self.seq_keys) == 2

    @property
    def is_combo(self) -> bool:
        return bool(self.combo)

    @property
    def is_empty(self) -> bool:
        return not self.combo and not self.seq_keys


def _normalize_shortcut_str(shortcut_str: str) -> str:
    """Convert legacy ``2x<token>`` to sequential ``<token>,<token>``."""
    if shortcut_str.startswith("2x"):
        token = shortcut_str[2:]
        return f"{token},{token}"
    return shortcut_str


# ── Per-shortcut runtime state ────────────────────────────────

class _SeqState:
    """Tracks the state machine for ONE sequential shortcut.

    Each sequential shortcut gets its own instance so they don't
    interfere with each other.
    """

    def __init__(self):
        self.armed = False        # True after the first key was pressed
        self.armed_time: float = 0.0

    def reset(self):
        self.armed = False
        self.armed_time = 0.0


# ── Shortcut string parsing: evdev ────────────────────────────

# Maps built lazily on first call and cached at module level.
_evdev_token_map: Optional[dict] = None
_evdev_char_map: Optional[dict] = None


def _build_evdev_maps():
    global _evdev_token_map, _evdev_char_map
    if _evdev_token_map is not None:
        return
    from evdev import ecodes

    _evdev_token_map = {
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
        _evdev_token_map[f"<f{i}>"] = [getattr(ecodes, f"KEY_F{i}")]

    _evdev_char_map = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        _evdev_char_map[c] = getattr(ecodes, f"KEY_{c.upper()}")
    for d in "0123456789":
        _evdev_char_map[d] = getattr(ecodes, f"KEY_{d}")
    _evdev_char_map["-"] = ecodes.KEY_MINUS
    _evdev_char_map["="] = ecodes.KEY_EQUAL
    _evdev_char_map["["] = ecodes.KEY_LEFTBRACE
    _evdev_char_map["]"] = ecodes.KEY_RIGHTBRACE
    _evdev_char_map[";"] = ecodes.KEY_SEMICOLON
    _evdev_char_map["'"] = ecodes.KEY_APOSTROPHE
    _evdev_char_map["."] = ecodes.KEY_DOT
    _evdev_char_map["/"] = ecodes.KEY_SLASH
    _evdev_char_map["\\"] = ecodes.KEY_BACKSLASH
    _evdev_char_map["`"] = ecodes.KEY_GRAVE


def _resolve_evdev_token(token: str) -> int | None:
    """Resolve a single config token to a canonical evdev key code."""
    _build_evdev_maps()
    assert _evdev_token_map is not None and _evdev_char_map is not None
    token = token.strip().lower()
    if token in _evdev_token_map:
        return _evdev_token_map[token][0]  # left variant
    if token in _evdev_char_map:
        return _evdev_char_map[token]
    if token.startswith("<") and token.endswith(">"):
        from evdev import ecodes
        code = getattr(ecodes, f"KEY_{token[1:-1].upper()}", None)
        if code is not None:
            return code
    log.warning("Unknown shortcut token: %s", token)
    return None


def _parse_shortcut_evdev(shortcut_str: str) -> _ShortcutConfig:
    """Parse a config-format shortcut string into an evdev _ShortcutConfig."""
    if not shortcut_str:
        return _ShortcutConfig()

    shortcut_str = _normalize_shortcut_str(shortcut_str)

    if "," in shortcut_str:
        halves = shortcut_str.split(",", 1)
        first = _resolve_evdev_token(halves[0])
        second = _resolve_evdev_token(halves[1])
        if first is not None and second is not None:
            return _ShortcutConfig(seq_keys=(first, second))
        return _ShortcutConfig()

    parts = shortcut_str.split("+")
    codes = set()
    for part in parts:
        code = _resolve_evdev_token(part)
        if code is not None:
            codes.add(code)
    if codes:
        return _ShortcutConfig(combo=frozenset(codes))
    return _ShortcutConfig()


# ── Modifier normalisation (evdev) ────────────────────────────

_EVDEV_MODIFIER_PAIRS: dict[int, int] = {}


def _init_modifier_pairs():
    global _EVDEV_MODIFIER_PAIRS
    if _EVDEV_MODIFIER_PAIRS:
        return
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


# ── Linux session detection ───────────────────────────────────

_SESSION_TYPE: Optional[str] = None

if _SYSTEM == "Linux":
    _SESSION_TYPE = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if not _SESSION_TYPE:
        _SESSION_TYPE = "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"


def _evdev_has_devices() -> bool:
    """Check if we can open any keyboard devices via evdev."""
    try:
        import evdev
        from evdev import ecodes

        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                has_keys = (
                    ecodes.EV_KEY in caps
                    and ecodes.KEY_A in caps[ecodes.EV_KEY]
                    and ecodes.KEY_Z in caps[ecodes.EV_KEY]
                )
                dev.close()
                if has_keys:
                    return True
            except (PermissionError, OSError):
                continue
    except ImportError:
        pass
    return False


# ── Public helpers for UI warnings ────────────────────────────

def needs_evdev(shortcut_str: str) -> bool:
    """Return True if *shortcut_str* requires evdev to work.

    On Wayland, pynput can only see key events that involve a modifier
    (Ctrl, Alt, Shift, Super).  Shortcuts that are sequential combos or
    plain-key combos without any modifier will NOT fire via pynput.
    """
    if not shortcut_str:
        return False

    shortcut_str = _normalize_shortcut_str(shortcut_str)

    _MODIFIER_TOKENS = {
        "<ctrl>", "<shift>", "<alt>", "<super>", "<cmd>",
    }

    # Sequential combos never work on pynput/Wayland.
    if "," in shortcut_str:
        return True

    # Simultaneous combo — check if at least one part is a modifier.
    parts = [p.strip().lower() for p in shortcut_str.split("+")]
    has_modifier = any(p in _MODIFIER_TOKENS for p in parts)
    return not has_modifier


def is_wayland_without_evdev() -> bool:
    """Return True if we're on Wayland and evdev can't access devices.

    When True, only modifier-based simultaneous combos will work.
    """
    return _SYSTEM == "Linux" and _SESSION_TYPE == "wayland" and not _evdev_has_devices()


# ── Linux evdev backend ───────────────────────────────────────

class _EvdevHotkeyListener:
    """Listen for global hotkeys using evdev (Linux)."""

    def __init__(self):
        self._toggle_cfg = _ShortcutConfig()
        self._ptt_cfg = _ShortcutConfig()
        self._current_keys: set[int] = set()

        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_press: Optional[Callable[[], None]] = None
        self.on_ptt_release: Optional[Callable[[], None]] = None

        # Per-shortcut sequential state (each gets its own so they
        # don't clobber each other).
        self._toggle_seq = _SeqState()
        self._ptt_seq = _SeqState()

        # Combo latch flags — prevent re-firing while keys are held.
        self._toggle_combo_active = False
        self._ptt_combo_active = False

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stop_pipe_r: Optional[int] = None
        self._stop_pipe_w: Optional[int] = None

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        _init_modifier_pairs()
        self._toggle_cfg = _parse_shortcut_evdev(toggle_shortcut)
        self._ptt_cfg = _parse_shortcut_evdev(ptt_shortcut)
        self._toggle_seq.reset()
        self._ptt_seq.reset()
        log.debug("Toggle config: %s", self._toggle_cfg)
        log.debug("PTT config: %s", self._ptt_cfg)

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
        self._toggle_seq.reset()
        self._ptt_seq.reset()
        self._toggle_combo_active = False
        self._ptt_combo_active = False

    @staticmethod
    def _normalize_key(code: int) -> int:
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

                            if event.value == 1:  # key down
                                self._current_keys.add(code)
                                self._on_key_down(code)
                            elif event.value == 0:  # key up
                                self._on_key_up(code)
                                self._current_keys.discard(code)
                            # value == 2 is auto-repeat — ignored
                    except OSError:
                        log.debug("Device %s disconnected", dev.path)
                        devices.remove(dev)
        finally:
            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass

    @staticmethod
    def _check_seq(cfg: _ShortcutConfig, state: _SeqState, code: int) -> bool:
        """Return True if *code* completes the sequential shortcut.

        For double-tap (first_key == second_key): the first press arms,
        the second press fires and resets.  Because we return True before
        the re-arm block, a third tap does NOT re-fire.
        """
        if not cfg.is_sequential:
            return False

        first_key, second_key = cfg.seq_keys
        now = time.monotonic()

        if code == second_key and state.armed and (now - state.armed_time) <= _SEQ_WINDOW:
            state.reset()
            return True

        if code == first_key:
            state.armed = True
            state.armed_time = now

        return False

    @staticmethod
    def _combo_matches(cfg: _ShortcutConfig, current_keys: set[int]) -> bool:
        return cfg.is_combo and cfg.combo.issubset(current_keys)

    def _on_key_down(self, code: int) -> None:
        # ── Toggle shortcut ──
        if self._toggle_cfg.is_sequential:
            if self._check_seq(self._toggle_cfg, self._toggle_seq, code):
                with self._lock:
                    if self.on_toggle:
                        self.on_toggle()
        elif self._toggle_cfg.is_combo:
            if self._combo_matches(self._toggle_cfg, self._current_keys):
                if not self._toggle_combo_active:
                    self._toggle_combo_active = True
                    with self._lock:
                        if self.on_toggle:
                            self.on_toggle()

        # ── PTT shortcut ──
        if self._ptt_cfg.is_sequential:
            if self._check_seq(self._ptt_cfg, self._ptt_seq, code):
                with self._lock:
                    if not self._ptt_combo_active:
                        self._ptt_combo_active = True
                        if self.on_ptt_press:
                            self.on_ptt_press()
        elif self._ptt_cfg.is_combo:
            if self._combo_matches(self._ptt_cfg, self._current_keys):
                if not self._ptt_combo_active:
                    self._ptt_combo_active = True
                    with self._lock:
                        if self.on_ptt_press:
                            self.on_ptt_press()

    def _on_key_up(self, code: int) -> None:
        # PTT release (combo)
        if self._ptt_combo_active and self._ptt_cfg.is_combo:
            if code in self._ptt_cfg.combo:
                self._ptt_combo_active = False
                with self._lock:
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # PTT release (sequential — release the second key)
        if self._ptt_combo_active and self._ptt_cfg.is_sequential:
            if code == self._ptt_cfg.seq_keys[1]:
                self._ptt_combo_active = False
                with self._lock:
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # Toggle combo latch reset
        if self._toggle_combo_active and self._toggle_cfg.is_combo:
            if code in self._toggle_cfg.combo:
                self._toggle_combo_active = False


# ── pynput backend (macOS / Windows / X11) ────────────────────

class _PynputHotkeyListener:
    """Listen for global hotkeys using pynput."""

    def __init__(self):
        self._listener = None
        self._toggle_cfg = _ShortcutConfig()
        self._ptt_cfg = _ShortcutConfig()
        self._current_keys: set = set()

        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_press: Optional[Callable[[], None]] = None
        self.on_ptt_release: Optional[Callable[[], None]] = None

        self._toggle_seq = _SeqState()
        self._ptt_seq = _SeqState()
        self._toggle_combo_active = False
        self._ptt_combo_active = False
        self._lock = threading.Lock()

    def _parse_shortcut(self, shortcut_str: str) -> _ShortcutConfig:
        from pynput import keyboard

        if not shortcut_str:
            return _ShortcutConfig()

        shortcut_str = _normalize_shortcut_str(shortcut_str)

        def _key_attr(name: str):
            # Some Key attributes are backend/platform dependent (e.g. macOS
            # may not expose Key.insert). Resolve defensively so startup never
            # crashes while parsing shortcuts.
            return getattr(keyboard.Key, name, None)

        key_map = {
            "<ctrl>": _key_attr("ctrl_l"),
            "<shift>": _key_attr("shift_l"),
            "<alt>": _key_attr("alt_l"),
            "<cmd>": _key_attr("cmd"),
            "<super>": _key_attr("cmd"),
            "<space>": _key_attr("space"),
            "<enter>": _key_attr("enter"),
            "<tab>": _key_attr("tab"),
            "<backspace>": _key_attr("backspace"),
            "<delete>": _key_attr("delete"),
            "<home>": _key_attr("home"),
            "<end>": _key_attr("end"),
            "<page_up>": _key_attr("page_up"),
            "<page_down>": _key_attr("page_down"),
            "<up>": _key_attr("up"),
            "<down>": _key_attr("down"),
            "<left>": _key_attr("left"),
            "<right>": _key_attr("right"),
            "<insert>": _key_attr("insert"),
            "<pause>": _key_attr("pause"),
            "<print_screen>": _key_attr("print_screen"),
            "<scroll_lock>": _key_attr("scroll_lock"),
            "<caps_lock>": _key_attr("caps_lock"),
            "<num_lock>": _key_attr("num_lock"),
        }
        for i in range(1, 13):
            key_map[f"<f{i}>"] = _key_attr(f"f{i}")

        # Remove tokens unavailable on the active platform/backend.
        key_map = {token: key for token, key in key_map.items() if key is not None}

        def _resolve(token: str):
            token = token.strip().lower()
            if token in key_map:
                return key_map[token]
            if len(token) == 1:
                return keyboard.KeyCode.from_char(token)
            if token.startswith("<") and token.endswith(">"):
                try:
                    return keyboard.Key[token[1:-1]]
                except KeyError:
                    pass
            log.warning("Unknown shortcut token (pynput): %s", token)
            return None

        if "," in shortcut_str:
            halves = shortcut_str.split(",", 1)
            first = _resolve(halves[0])
            second = _resolve(halves[1])
            if first is not None and second is not None:
                return _ShortcutConfig(seq_keys=(first, second))
            return _ShortcutConfig()

        parts = shortcut_str.split("+")
        keys = set()
        for part in parts:
            key = _resolve(part)
            if key is not None:
                keys.add(key)
        if keys:
            return _ShortcutConfig(combo=frozenset(keys))
        return _ShortcutConfig()

    def set_shortcuts(self, toggle_shortcut: str, ptt_shortcut: str) -> None:
        self._toggle_cfg = self._parse_shortcut(toggle_shortcut)
        self._ptt_cfg = self._parse_shortcut(ptt_shortcut)
        self._toggle_seq.reset()
        self._ptt_seq.reset()

    def start(self) -> None:
        from pynput import keyboard

        if self._listener is not None:
            self.stop()

        # On macOS, verify the process is trusted before creating the
        # listener.  pynput's Quartz backend will segfault if
        # CGEventTapCreate returns NULL (untrusted process).
        if _SYSTEM == "Darwin":
            try:
                import ctypes, ctypes.util
                path = ctypes.util.find_library("ApplicationServices")
                if not path:
                    path = (
                        "/System/Library/Frameworks"
                        "/ApplicationServices.framework/ApplicationServices"
                    )
                lib = ctypes.cdll.LoadLibrary(path)
                lib.AXIsProcessTrusted.restype = ctypes.c_bool
                if not lib.AXIsProcessTrusted():
                    log.warning(
                        "Process is not trusted for Accessibility — "
                        "skipping pynput listener to avoid segfault"
                    )
                    return
            except Exception:
                pass  # can't check — proceed cautiously

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            try:
                self._listener.join(timeout=2)
            except Exception:
                pass
            self._listener = None
        self._current_keys.clear()
        self._toggle_seq.reset()
        self._ptt_seq.reset()
        self._toggle_combo_active = False
        self._ptt_combo_active = False

    def _normalize_key(self, key):
        from pynput import keyboard
        normalization = {}
        ctrl_r = getattr(keyboard.Key, "ctrl_r", None)
        ctrl_l = getattr(keyboard.Key, "ctrl_l", None)
        shift_r = getattr(keyboard.Key, "shift_r", None)
        shift_l = getattr(keyboard.Key, "shift_l", None)
        alt_r = getattr(keyboard.Key, "alt_r", None)
        alt_gr = getattr(keyboard.Key, "alt_gr", None)
        alt_l = getattr(keyboard.Key, "alt_l", None)

        if ctrl_r is not None and ctrl_l is not None:
            normalization[ctrl_r] = ctrl_l
        if shift_r is not None and shift_l is not None:
            normalization[shift_r] = shift_l
        if alt_r is not None and alt_l is not None:
            normalization[alt_r] = alt_l
        if alt_gr is not None and alt_l is not None:
            normalization[alt_gr] = alt_l
        return normalization.get(key, key)

    @staticmethod
    def _check_seq(cfg: _ShortcutConfig, state: _SeqState, key) -> bool:
        if not cfg.is_sequential:
            return False
        first_key, second_key = cfg.seq_keys
        now = time.monotonic()

        if key == second_key and state.armed and (now - state.armed_time) <= _SEQ_WINDOW:
            state.reset()
            return True

        if key == first_key:
            state.armed = True
            state.armed_time = now

        return False

    @staticmethod
    def _combo_matches(cfg: _ShortcutConfig, current_keys: set) -> bool:
        return cfg.is_combo and cfg.combo.issubset(current_keys)

    def _on_press(self, key) -> None:
        normalized = self._normalize_key(key)
        self._current_keys.add(normalized)

        # Toggle
        if self._toggle_cfg.is_sequential:
            if self._check_seq(self._toggle_cfg, self._toggle_seq, normalized):
                with self._lock:
                    if self.on_toggle:
                        self.on_toggle()
        elif self._toggle_cfg.is_combo:
            if self._combo_matches(self._toggle_cfg, self._current_keys):
                if not self._toggle_combo_active:
                    self._toggle_combo_active = True
                    with self._lock:
                        if self.on_toggle:
                            self.on_toggle()

        # PTT
        if self._ptt_cfg.is_sequential:
            if self._check_seq(self._ptt_cfg, self._ptt_seq, normalized):
                with self._lock:
                    if not self._ptt_combo_active:
                        self._ptt_combo_active = True
                        if self.on_ptt_press:
                            self.on_ptt_press()
        elif self._ptt_cfg.is_combo:
            if self._combo_matches(self._ptt_cfg, self._current_keys):
                if not self._ptt_combo_active:
                    self._ptt_combo_active = True
                    with self._lock:
                        if self.on_ptt_press:
                            self.on_ptt_press()

    def _on_release(self, key) -> None:
        normalized = self._normalize_key(key)

        # PTT release (combo)
        if self._ptt_combo_active and self._ptt_cfg.is_combo:
            if normalized in self._ptt_cfg.combo:
                self._ptt_combo_active = False
                with self._lock:
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # PTT release (sequential)
        if self._ptt_combo_active and self._ptt_cfg.is_sequential:
            if normalized == self._ptt_cfg.seq_keys[1]:
                self._ptt_combo_active = False
                with self._lock:
                    if self.on_ptt_release:
                        self.on_ptt_release()

        # Toggle combo latch reset
        if self._toggle_combo_active and self._toggle_cfg.is_combo:
            if normalized in self._toggle_cfg.combo:
                self._toggle_combo_active = False

        self._current_keys.discard(normalized)


# ── Public facade ─────────────────────────────────────────────

class HotkeyManager:
    """Manages global hotkeys — auto-selects the best backend."""

    def __init__(self):
        if _SYSTEM == "Linux":
            if _evdev_has_devices():
                self._backend = _EvdevHotkeyListener()
                log.info("Using evdev backend for global hotkeys")
            else:
                self._backend = _PynputHotkeyListener()
                if _SESSION_TYPE == "wayland":
                    log.warning(
                        "evdev unavailable — using pynput on Wayland. "
                        "Only modifier-based combos (Ctrl+X, Alt+V, etc.) will work. "
                        "For full support: sudo usermod -aG input $USER  (then re-login)"
                    )
                else:
                    log.info("Using pynput backend (X11)")
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
