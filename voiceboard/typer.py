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
import re
import subprocess
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
        except ImportError:
            log.warning("dbus-python not installed — Wayland typing unavailable")
            return False

        try:
            self._bus = dbus.SessionBus(private=True)
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

            # Launch dbus-monitor to detect async Response signals
            monitor = subprocess.Popen(
                [
                    "dbus-monitor", "--session",
                    "type='signal',"
                    "interface='org.freedesktop.portal.Request',"
                    "member='Response'",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            monitor_lines: list[str] = []

            def _read_monitor():
                assert monitor.stdout is not None
                for line in monitor.stdout:
                    monitor_lines.append(line.strip())

            monitor_thread = threading.Thread(target=_read_monitor, daemon=True)
            monitor_thread.start()

            # ── CreateSession ──
            self._rd.CreateSession({
                "handle_token": dbus.String(f"vb_{pid}"),
                "session_handle_token": dbus.String(session_token),
            })
            time.sleep(0.5)

            # ── SelectDevices — keyboard only ──
            self._rd.SelectDevices(
                dbus.ObjectPath(self._session_path),
                {
                    "handle_token": dbus.String(f"vb_sel_{pid}"),
                    "types": dbus.UInt32(1),  # 1 = keyboard
                },
            )
            time.sleep(0.5)

            # ── Start — may trigger a one-time permission dialog ──
            self._rd.Start(
                dbus.ObjectPath(self._session_path),
                "",  # parent window
                {"handle_token": dbus.String(start_token)},
            )

            # Wait for the Start Response signal (up to 30 s for user approval)
            deadline = time.monotonic() + 30
            approved = False
            while time.monotonic() < deadline:
                full = "\n".join(monitor_lines)
                if start_token in full and "uint32 0" in full:
                    approved = True
                    break
                time.sleep(0.2)

            monitor.terminate()
            try:
                monitor.wait(timeout=2)
            except subprocess.TimeoutExpired:
                monitor.kill()

            if not approved:
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


# ── Public API ─────────────────────────────────────────────────

def type_text(text: str) -> None:
    """Inject *text* into the currently focused input field, as if the
    user typed it on a physical keyboard.

    Thread-safe.  Automatically selects the best backend for the
    current platform.
    """
    if not text:
        return
    _get_typer().type_text(text)
