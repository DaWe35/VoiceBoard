"""Entry point for VoiceBoard: python -m voiceboard"""

import os
import sys
from ctypes.util import find_library as _original_find_library


def _fix_ssl_for_frozen():
    """Ensure SSL certificates are found in PyInstaller builds."""
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller bundle — point SSL at the bundled certifi CA
        bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
        cert_file = os.path.join(bundle_dir, 'certifi', 'cacert.pem')
        if os.path.exists(cert_file):
            os.environ.setdefault('SSL_CERT_FILE', cert_file)


def _fix_portaudio_for_frozen():
    """Ensure PortAudio library is found in PyInstaller builds on Linux.
    
    Patches ctypes.util.find_library to also check sys._MEIPASS (where PyInstaller
    extracts bundled libraries) before falling back to system paths.
    """
    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
        import ctypes.util
        import glob
        
        bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
        
        def _patched_find_library(name):
            # First check the PyInstaller bundle directory
            # Look for lib{name}.so* patterns
            patterns = [
                os.path.join(bundle_dir, f'lib{name}.so'),
                os.path.join(bundle_dir, f'lib{name}.so.*'),
            ]
            for pattern in patterns:
                matches = glob.glob(pattern)
                if matches:
                    # Return full path - sounddevice's _ffi.dlopen() can load libraries
                    # by full path, and this ensures it finds the bundled library
                    return matches[0]
            
            # Fall back to original find_library
            return _original_find_library(name)
        
        # Patch the function in ctypes.util module
        ctypes.util.find_library = _patched_find_library
        
        # Also set LD_LIBRARY_PATH as a backup
        current_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
        if bundle_dir not in current_ld_path:
            new_ld_path = f"{bundle_dir}:{current_ld_path}" if current_ld_path else bundle_dir
            os.environ['LD_LIBRARY_PATH'] = new_ld_path


def main():
    _fix_ssl_for_frozen()
    _fix_portaudio_for_frozen()
    from voiceboard.app import VoiceBoardApp
    app = VoiceBoardApp()
    raise SystemExit(app.run())


if __name__ == "__main__":
    main()
