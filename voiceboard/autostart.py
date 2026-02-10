"""Cross-platform auto-start management for VoiceBoard.

Supports:
  - Linux:   XDG autostart desktop entry (~/.config/autostart/)
  - macOS:   LaunchAgent plist (~/Library/LaunchAgents/)
  - Windows: Registry key (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run)
"""

import os
import sys
from pathlib import Path


APP_NAME = "VoiceBoard"

# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_executable_command() -> list[str]:
    """Return the command list used to launch VoiceBoard at OS login.

    Handles both frozen (PyInstaller) and regular Python installs.
    The ``--autostart`` flag is appended so the app knows it was launched
    by the OS and should start minimized to the tray.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller single-file executable
        return [sys.executable, "--autostart"]

    # Installed via pip / pipx — look for the console-script wrapper
    import shutil

    wrapper = shutil.which("voiceboard") or shutil.which("voiceboard-gui")
    if wrapper:
        return [wrapper, "--autostart"]

    # Fallback: run as a Python module
    return [sys.executable, "-m", "voiceboard", "--autostart"]


# ── Linux (XDG autostart) ───────────────────────────────────────────────────


def _linux_desktop_path() -> Path:
    autostart_dir = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ) / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    return autostart_dir / "voiceboard.desktop"


def _linux_enable() -> None:
    cmd = _get_executable_command()
    exec_line = " ".join(cmd)
    content = (
        "[Desktop Entry]\n"
        f"Name={APP_NAME}\n"
        f"Exec={exec_line}\n"
        "Type=Application\n"
        "X-GNOME-Autostart-enabled=true\n"
        f"Comment=Start {APP_NAME} on login\n"
        "Terminal=false\n"
    )
    _linux_desktop_path().write_text(content)


def _linux_disable() -> None:
    path = _linux_desktop_path()
    if path.exists():
        path.unlink()


def _linux_is_enabled() -> bool:
    return _linux_desktop_path().exists()


# ── macOS (LaunchAgent) ─────────────────────────────────────────────────────


def _macos_plist_path() -> Path:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    return launch_agents / "com.voiceboard.app.plist"


def _macos_enable() -> None:
    cmd = _get_executable_command()
    args_xml = "\n".join(f"    <string>{arg}</string>" for arg in cmd)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        "  <string>com.voiceboard.app</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"{args_xml}\n"
        "  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )
    _macos_plist_path().write_text(plist)


def _macos_disable() -> None:
    path = _macos_plist_path()
    if path.exists():
        path.unlink()


def _macos_is_enabled() -> bool:
    return _macos_plist_path().exists()


# ── Windows (Registry) ──────────────────────────────────────────────────────


_WIN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_REG_VALUE = APP_NAME


def _windows_enable() -> None:
    import winreg  # type: ignore[import-not-found]

    cmd = _get_executable_command()
    # Wrap each part in quotes if it contains spaces
    value = " ".join(f'"{c}"' if " " in c else c for c in cmd)

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _WIN_REG_VALUE, 0, winreg.REG_SZ, value)


def _windows_disable() -> None:
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _WIN_REG_VALUE)
    except FileNotFoundError:
        pass  # already removed


def _windows_is_enabled() -> bool:
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _WIN_REG_VALUE)
            return True
    except FileNotFoundError:
        return False


# ── Public API ───────────────────────────────────────────────────────────────


def set_autostart(enabled: bool) -> None:
    """Enable or disable auto-start on the current platform."""
    if sys.platform == "linux":
        _linux_enable() if enabled else _linux_disable()
    elif sys.platform == "darwin":
        _macos_enable() if enabled else _macos_disable()
    elif sys.platform == "win32":
        _windows_enable() if enabled else _windows_disable()


def is_autostart_enabled() -> bool:
    """Check whether auto-start is currently configured on the current platform."""
    if sys.platform == "linux":
        return _linux_is_enabled()
    elif sys.platform == "darwin":
        return _macos_is_enabled()
    elif sys.platform == "win32":
        return _windows_is_enabled()
    return False
