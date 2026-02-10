# VoiceBoard

Cross-platform realtime voice keyboard. Speak into your mic and have the text typed into any focused application.

Features:
- Windows, macOS, Linux support (Wayland and X11 supported)
- Plug-and-play. No dependencies required, it just works.
- Real-time transcription (words appear as you speak)
- Works well with any languages (not like Whisper)
- Shortcuts for toggle and push-to-talk mode


## Development Setup

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install package in editable mode
pip install -e .

# Run the app
python -m voiceboard
```

## Build Single Executable

PyInstaller must run **on** the target OS — it cannot cross-compile.

```bash
pip install pyinstaller
pyinstaller voiceboard.spec
# Output: dist/VoiceBoard      (Linux/macOS)
# Output: dist/VoiceBoard.exe  (Windows)
```

### Build via GitHub Actions (all platforms)

Push a version tag to trigger automated builds for Linux, Windows, and macOS:

```bash
git tag v1.0.0
git push origin main --tags
```

This creates a GitHub Release with executables for all three platforms.

Builds also run automatically on every push to `main` (artifacts downloadable from the Actions run page). You can trigger a build manually from the **Actions** tab → **Build Executables** → **Run workflow**.

## Usage

1. Launch the app and paste your OpenAI API key in the settings panel.
2. Press the **Start** button or use a global hotkey to begin recording.
3. Speak — the audio level bar shows input in real time.
4. Stop recording — audio is sent to OpenAI and the transcribed text is typed into the focused field.

### Default Hotkeys

| Shortcut | Mode |
|---|---|
| `Ctrl+Shift+V` | Toggle — press to start, press again to stop & type |
| `Ctrl+Shift+B` | Push-to-talk — hold to record, release to transcribe & type |
