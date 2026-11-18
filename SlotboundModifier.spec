# -*- mode: python ; coding: utf-8 -*-
# Build from the repository root: python -m PyInstaller --noconfirm SlotboundModifier.spec
a = Analysis(
    ['trainer_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'numpy', 'PIL', 'pefile', 'cryptography', 'ljd',
              'win32api', 'win32evtlog', 'win32evtlogutil', 'pywintypes'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='AnselSlotboundTrainer',
    version='version_info.txt',
    icon='assets/app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
