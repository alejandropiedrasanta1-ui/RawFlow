# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['RawFlow.py'],
    pathex=[],
    binaries=[('build/rfui', '.')],
    datas=[('rawflow/cineslide_run.py', 'rawflow'), ('rawflow/r4m_worker.js', 'rawflow')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RawFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['build/RawFlow.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RawFlow',
)
app = BUNDLE(
    coll,
    name='RawFlow.app',
    icon='build/RawFlow.icns',
    bundle_identifier='com.rawflow.app',
)
