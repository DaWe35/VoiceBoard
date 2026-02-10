# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for VoiceBoard single-executable build."""

import sys
import certifi
from ctypes.util import find_library
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Bundle SSL certificate authority bundle so HTTPS/WSS connections work
_certifi_pem = certifi.where()

# ---------------------------------------------------------------------------
# Bundle PortAudio so users don't need it installed separately.
#
# Linux:   find the system libportaudio via ctypes and ship it.
# Win/Mac: the `_sounddevice_data` pip package ships pre-built binaries;
#          collect_data_files pulls them in automatically.
# ---------------------------------------------------------------------------
_portaudio_binaries = []
_portaudio_datas = []

if sys.platform.startswith('linux'):
    import os
    import subprocess
    import glob
    
    # Try to find libportaudio.so via ldconfig (most reliable)
    _pa_path = None
    try:
        _ldconfig_out = subprocess.check_output(['ldconfig', '-p'], text=True, stderr=subprocess.DEVNULL)
        for _line in _ldconfig_out.splitlines():
            if 'libportaudio.so' in _line and '=>' in _line:
                _pa_path = _line.split('=>')[-1].strip()
                break
    except Exception:
        pass
    
    # Fallback: search common library paths
    if not _pa_path:
        for _libdir in ('/usr/lib', '/usr/lib/x86_64-linux-gnu', '/usr/lib/aarch64-linux-gnu', '/lib', '/lib/x86_64-linux-gnu'):
            _matches = glob.glob(os.path.join(_libdir, 'libportaudio.so*'))
            if _matches:
                # Prefer .so.2 over .so (the actual library, not symlink)
                _pa_path = sorted(_matches, key=lambda x: ('.so.' in x, x))[-1]
                break
    
    if _pa_path and os.path.isfile(_pa_path):
        # Resolve symlinks to get the actual library file
        _real_path = os.path.realpath(_pa_path)
        _portaudio_binaries.append((_real_path, '.'))
else:
    # Windows & macOS: _sounddevice_data ships the PortAudio binary
    try:
        _portaudio_datas = collect_data_files('_sounddevice_data')
    except Exception:
        pass

a = Analysis(
    ['voiceboard/__main__.py'],
    pathex=[],
    binaries=_portaudio_binaries,
    datas=[
        (_certifi_pem, 'certifi'),
    ] + collect_data_files('certifi') + _portaudio_datas,
    hiddenimports=[
        'PySide6.QtSvg',
        'certifi',
        'pynput.keyboard._xorg',
        'pynput.keyboard._win32',
        'pynput.keyboard._darwin',
        'pynput.mouse._xorg',
        'pynput.mouse._win32',
        'pynput.mouse._darwin',
        'dbus',
        'dbus.mainloop',
        'dbus.mainloop.glib',
    ] + collect_submodules('pynput'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='VoiceBoard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
