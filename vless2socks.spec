# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for vless2socks standalone single-file GUI with embedded bin."""

import os
import sys

block_cipher = None
root = os.path.dirname(os.path.abspath(SPEC))

CORE_IMPORTS = [
    'vless2socks',
    'vless2socks.backend',
    'vless2socks.config',
    'vless2socks.doctor',
    'vless2socks.ipcheck',
    'vless2socks.logging_setup',
    'vless2socks.noconsole',
    'vless2socks.paths',
    'vless2socks.protocol',
    'vless2socks.relay',
    'vless2socks.selftest',
    'vless2socks.socks5',
    'vless2socks.socks_client',
    'vless2socks.transport',
    'vless2socks.url',
    'vless2socks.vless',
    'vless2socks.xray',
    'vless2socks.xray.binary',
    'vless2socks.xray.config_builder',
    'vless2socks.xray.runner',
]

a = Analysis(
    [os.path.join(root, 'gui.py')],
    pathex=[root],
    binaries=[],
    datas=[
        (os.path.join(root, 'config.example.json'), '.'),
        (os.path.join(root, 'instances.example.json'), '.'),
        (os.path.join(root, 'settings.example.json'), '.'),
        (os.path.join(root, 'bin'), 'bin'),
    ],
    hiddenimports=[
        'backup_manager',
        'geo_ip',
        'settings_manager',
        'i18n',
        'tray_widget',
        'main',
        'tools',
        'tools.get_xray',
        'pystray',
        'PIL',
        'cryptography',
    ] + CORE_IMPORTS,
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
    name='vless2socks',
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
    icon=None,
)
