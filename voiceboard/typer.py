"""Text injection module for VoiceBoard.

Types transcribed text into the currently focused input field
using clipboard paste for maximum compatibility.
"""

import platform
import time
import pyperclip
from pynput.keyboard import Controller, Key


_keyboard = Controller()


def type_text(text: str) -> None:
    """
    Inject text into the currently active input field.

    Uses clipboard + paste shortcut for reliability across platforms.
    Preserves the previous clipboard content and restores it afterwards.
    """
    if not text:
        return

    # Save current clipboard
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        old_clipboard = ""

    try:
        # Put transcribed text on clipboard
        pyperclip.copy(text)
        time.sleep(0.05)

        # Paste using platform-appropriate shortcut
        modifier = Key.cmd if platform.system() == "Darwin" else Key.ctrl
        with _keyboard.pressed(modifier):
            _keyboard.press("v")
            _keyboard.release("v")

        time.sleep(0.1)
    finally:
        # Restore old clipboard after a short delay
        try:
            time.sleep(0.2)
            pyperclip.copy(old_clipboard)
        except Exception:
            pass
