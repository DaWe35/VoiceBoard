# VoiceBoard

Cross-platform voice keyboard powered by OpenAI transcription. Speak into your mic and have the text typed into any focused application.

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

```bash
pip install pyinstaller
pyinstaller voiceboard.spec
# Output: dist/VoiceBoard
```

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
