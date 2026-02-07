"""Entry point for VoiceBoard: python -m voiceboard"""

from voiceboard.app import VoiceBoardApp


def main():
    app = VoiceBoardApp()
    raise SystemExit(app.run())


if __name__ == "__main__":
    main()
