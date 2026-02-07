"""Configuration management for VoiceBoard."""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict


def _config_dir() -> Path:
    """Get platform-appropriate config directory."""
    import platform
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_path = base / "voiceboard"
    config_path.mkdir(parents=True, exist_ok=True)
    return config_path


CONFIG_FILE = _config_dir() / "config.json"


@dataclass
class AppConfig:
    openai_api_key: str = ""
    toggle_shortcut: str = "<ctrl>+<shift>+v"
    ptt_shortcut: str = "<ctrl>+<shift>+b"
    language: str = ""
    start_minimized: bool = False
    auto_start: bool = False
    sample_rate: int = 24000
    model: str = "gpt-4o-mini-transcribe"

    def save(self) -> None:
        """Persist config to disk."""
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "AppConfig":
        """Load config from disk, or return defaults."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                # Filter to only known fields
                known = {f.name for f in cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in data.items() if k in known}
                return cls(**filtered)
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()
