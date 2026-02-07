"""Entry point for VoiceBoard: python -m voiceboard"""

import os
import sys


def _fix_ssl_for_frozen():
    """Ensure SSL certificates are found in PyInstaller builds."""
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller bundle — point SSL at the bundled certifi CA
        bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
        cert_file = os.path.join(bundle_dir, 'certifi', 'cacert.pem')
        if os.path.exists(cert_file):
            os.environ.setdefault('SSL_CERT_FILE', cert_file)


def main():
    _fix_ssl_for_frozen()
    from voiceboard.app import VoiceBoardApp
    app = VoiceBoardApp()
    raise SystemExit(app.run())


if __name__ == "__main__":
    main()
