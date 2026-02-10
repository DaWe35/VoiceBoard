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
    _pa = find_library('portaudio')
    if _pa:
        import ctypes.util, os, subprocess
        # find_library returns e.g. 'libportaudio.so.2'; resolve full path
        try:
            _pa_path = subprocess.check_output(
                ['ldconfig', '-p'], text=True,
            )
            for _line in _pa_path.splitlines():
                if _pa in _line and '=>' in _line:
                    _resolved = _line.split('=>')[-1].strip()
                    _portaudio_binaries.append((_resolved, '.'))
                    break
        except Exception:
            pass
        if not _portaudio_binaries:
            # Fallback: common paths
            for _candidate in (
                f'/usr/lib/{_pa}',
                f'/usr/lib/x86_64-linux-gnu/{_pa}',
                f'/usr/lib/aarch64-linux-gnu/{_pa}',
            ):
                if os.path.isfile(_candidate):
                    _portaudio_binaries.append((_candidate, '.'))
                    break
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
