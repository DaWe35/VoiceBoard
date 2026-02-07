# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for VoiceBoard single-executable build."""

import sys
import certifi
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Bundle SSL certificate authority bundle so HTTPS/WSS connections work
_certifi_pem = certifi.where()

a = Analysis(
    ['voiceboard/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        (_certifi_pem, 'certifi'),
    ] + collect_data_files('certifi'),
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
