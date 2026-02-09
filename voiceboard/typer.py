"""Text injection module for VoiceBoard.

Types transcribed text into the currently focused input field.

Platform strategies:
  - Linux (Wayland) → XDG RemoteDesktop portal via dbus-python
    (NotifyKeyboardKeysym — layout-independent, Unicode-capable)
  - Linux (X11) / macOS / Windows → pynput keyboard Controller

Text is injected in real-time as transcription deltas arrive.
"""

import logging
import os
import platform
import queue
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

_SYSTEM = platform.system()  # "Linux", "Darwin", "Windows"
_SESSION_TYPE: Optional[str] = None

if _SYSTEM == "Linux":
    _SESSION_TYPE = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if not _SESSION_TYPE:
        _SESSION_TYPE = "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"


# ── Wayland: XDG RemoteDesktop Portal ──────────────────────────

class _WaylandPortalTyper:
    """Type text via the XDG RemoteDesktop portal (Wayland).

    Uses ``NotifyKeyboardKeysym`` which is keyboard-layout-independent
    and handles Unicode characters natively.  The portal session is
    established once and kept alive for the lifetime of the process.
    """

    def __init__(self):
        self._session_path: Optional[str] = None
        self._rd = None          # portal RemoteDesktop D-Bus interface
        self._bus = None         # dbus connection (kept alive)
        self._lock = threading.Lock()
        self._setup_done = False

    # ── public ──

    def setup(self) -> bool:
        """Establish a RemoteDesktop portal session.  Returns True on success."""
        if self._setup_done:
            return self._session_path is not None
        self._setup_done = True

        try:
            import dbus  # type: ignore[import-untyped]
            from dbus.mainloop.glib import DBusGMainLoop  # type: ignore[import-untyped]
        except ImportError:
            log.warning("dbus-python not installed — Wayland typing unavailable")
            return False

        try:
            # Use the GLib main loop so we can receive D-Bus signals natively
            # (no external dbus-monitor subprocess needed).
            DBusGMainLoop(set_as_default=True)
            self._bus = dbus.SessionBus()
            portal = self._bus.get_object(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
            )
            self._rd = dbus.Interface(portal, "org.freedesktop.portal.RemoteDesktop")

            pid = os.getpid()
            bus_name = self._bus.get_unique_name()
            sender = bus_name.replace(":", "").replace(".", "_")
            session_token = f"vb_s_{pid}"
            start_token = f"vb_start_{pid}"
            self._session_path = (
                f"/org/freedesktop/portal/desktop/session/{sender}/{session_token}"
            )

            # Shared state for the async Response signal handler
            response_event = threading.Event()
            response_result: list[int] = []  # will hold the response code

            def _on_response(response_code, results):
                response_result.append(response_code)
                response_event.set()

            # Load libglib-2.0 via ctypes to pump the D-Bus main loop.
            # dbus.mainloop.glib already links against it, so it is always
            # present — no PyGObject / gi dependency required.
            import ctypes
            import ctypes.util
            _glib_name = ctypes.util.find_library("glib-2.0")
            _glib = ctypes.CDLL(_glib_name) if _glib_name else None

            def _pump_glib(timeout: float) -> None:
                """Process pending GLib main-context events for *timeout* secs."""
                if _glib is None:
                    # Fallback: just sleep and hope the signal was delivered
                    time.sleep(timeout)
                    return
                # g_main_context_default() → GMainContext*
                _glib.g_main_context_default.restype = ctypes.c_void_p
                ctx = _glib.g_main_context_default()
                # g_main_context_iteration(ctx, may_block) → gboolean
                _glib.g_main_context_iteration.argtypes = [ctypes.c_void_p, ctypes.c_int]
                _glib.g_main_context_iteration.restype = ctypes.c_int
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    _glib.g_main_context_iteration(ctx, 0)  # non-blocking
                    if response_event.is_set():
                        break
                    time.sleep(0.05)

            def _wait_for_response(request_path: str, timeout: float = 30) -> bool:
                """Subscribe to the Response signal on *request_path* and block
                until it fires or *timeout* seconds elapse.  Returns True if
                the portal returned response code 0 (success)."""
                response_event.clear()
                response_result.clear()

                self._bus.add_signal_receiver(
                    _on_response,
                    signal_name="Response",
                    dbus_interface="org.freedesktop.portal.Request",
                    path=request_path,
                )

                _pump_glib(timeout)

                return bool(response_result and response_result[0] == 0)

            # ── CreateSession ──
            create_handle = self._rd.CreateSession({
                "handle_token": dbus.String(f"vb_{pid}"),
                "session_handle_token": dbus.String(session_token),
            })
            request_path = str(create_handle)
            if not _wait_for_response(request_path, timeout=5):
                log.warning("RemoteDesktop CreateSession was not acknowledged")
                self._session_path = None
                return False

            # ── SelectDevices — keyboard only ──
            select_handle = self._rd.SelectDevices(
                dbus.ObjectPath(self._session_path),
                {
                    "handle_token": dbus.String(f"vb_sel_{pid}"),
                    "types": dbus.UInt32(1),  # 1 = keyboard
                },
            )
            request_path = str(select_handle)
            if not _wait_for_response(request_path, timeout=5):
                log.warning("RemoteDesktop SelectDevices was not acknowledged")
                self._session_path = None
                return False

            # ── Start — may trigger a one-time permission dialog ──
            start_handle = self._rd.Start(
                dbus.ObjectPath(self._session_path),
                "",  # parent window
                {"handle_token": dbus.String(start_token)},
            )
            request_path = str(start_handle)
            if not _wait_for_response(request_path, timeout=30):
                log.warning("RemoteDesktop portal Start was not approved (timeout)")
                self._session_path = None
                return False

            log.info("RemoteDesktop portal session ready: %s", self._session_path)
            return True

        except Exception:
            log.exception("Failed to set up RemoteDesktop portal session")
            self._session_path = None
            return False

    def type_text(self, text: str) -> None:
        """Type *text* into the focused window via keysym events."""
        if not self._session_path or self._rd is None:
            return

        import dbus  # type: ignore[import-untyped]

        with self._lock:
            for ch in text:
                keysym = self._char_to_keysym(ch)
                try:
                    self._rd.NotifyKeyboardKeysym(
                        dbus.ObjectPath(self._session_path), {},
                        dbus.Int32(keysym), dbus.UInt32(1),  # press
                    )
                    self._rd.NotifyKeyboardKeysym(
                        dbus.ObjectPath(self._session_path), {},
                        dbus.Int32(keysym), dbus.UInt32(0),  # release
                    )
                except Exception:
                    log.exception("Portal keysym injection failed")
                    break

    def close(self) -> None:
        """Close the portal session."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._session_path = None
        self._setup_done = False

    # ── internal ──

    @staticmethod
    def _char_to_keysym(ch: str) -> int:
        """Convert a character to its XKB keysym value."""
        cp = ord(ch)
        if cp == 0x0A:          # newline → Return
            return 0xFF0D
        if cp == 0x09:          # tab → Tab
            return 0xFF09
        if cp == 0x08:          # backspace
            return 0xFF08
        if 0x20 <= cp <= 0x7E:  # ASCII printable (keysym == codepoint)
            return cp
        if 0xA0 <= cp <= 0xFF:  # Latin-1 supplement
            return cp
        # General Unicode → keysym = 0x0100_0000 + codepoint
        return 0x01000000 + cp


# ── pynput fallback (X11, macOS, Windows) ──────────────────────

class _PynputTyper:
    """Type text using pynput's keyboard Controller."""

    def __init__(self):
        from pynput.keyboard import Controller
        self._keyboard = Controller()
        self._lock = threading.Lock()

    def type_text(self, text: str) -> None:
        with self._lock:
            try:
                self._keyboard.type(text)
            except Exception:
                log.exception("pynput typing failed")


# ── Module-level singleton ─────────────────────────────────────

_typer: Optional[_WaylandPortalTyper | _PynputTyper] = None
_init_lock = threading.Lock()


def _get_typer():
    """Lazily initialise and return the platform-appropriate typer."""
    global _typer
    if _typer is not None:
        return _typer

    with _init_lock:
        if _typer is not None:          # double-check after lock
            return _typer

        if _SYSTEM == "Linux" and _SESSION_TYPE == "wayland":
            portal = _WaylandPortalTyper()
            if portal.setup():
                _typer = portal
                log.info("Using Wayland RemoteDesktop portal for typing")
                return _typer
            else:
                log.warning("Portal setup failed, falling back to pynput")

        # Fallback: pynput (X11, macOS, Windows, or portal failure)
        _typer = _PynputTyper()
        log.info("Using pynput for typing")
        return _typer


# ── Persistent typing worker ───────────────────────────────────

class _TypingWorker:
    """Serialises typing requests on a single persistent background thread.

    Spawning a new OS thread for every transcription delta is expensive and,
    on Windows, causes the pynput low-level keyboard hook to exceed its
    timeout — making Windows silently drop injected keystrokes after the
    first word.  A single long-lived worker thread avoids this entirely.
    """

    def __init__(self):
        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enqueue(self, text: str) -> None:
        """Schedule *text* to be typed.  Returns immediately."""
        self._queue.put(text)

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:        # poison pill → shut down
                break
            if text:
                _get_typer().type_text(text)


_worker: Optional[_TypingWorker] = None
_worker_lock = threading.Lock()


def _get_worker() -> _TypingWorker:
    """Lazily create the singleton typing worker."""
    global _worker
    if _worker is not None:
        return _worker
    with _worker_lock:
        if _worker is not None:
            return _worker
        _worker = _TypingWorker()
        return _worker


# ── Public API ─────────────────────────────────────────────────

def ensure_ready() -> None:
    """Eagerly initialise the platform typer (and trigger any permission
    dialogs, e.g. the Wayland RemoteDesktop portal prompt) so that the
    user is asked *now* rather than on the first transcription delta.

    Safe to call from any thread; repeated calls are no-ops.
    """
    _get_typer()


def type_text(text: str) -> None:
    """Inject *text* into the currently focused input field, as if the
    user typed it on a physical keyboard.

    Thread-safe.  Automatically selects the best backend for the
    current platform.
    """
    if not text:
        return
    _get_typer().type_text(text)


def enqueue_text(text: str) -> None:
    """Queue *text* to be typed on a persistent background thread.

    Unlike :func:`type_text` (which types synchronously on the calling
    thread), this function returns immediately and the actual keystroke
    injection happens on a single long-lived worker thread.  This avoids
    the overhead of spawning a new OS thread per delta and — critically
    on Windows — prevents the pynput keyboard hook from timing out and
    dropping injected keystrokes.
    """
    if not text:
        return
    _get_worker().enqueue(text)
