# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['lingmengwork_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('lingmengwork/web/static', 'lingmengwork/web/static'), ('config.toml', 'lingmengwork'), ('VERSION', '.')],
    hiddenimports=['lingmengwork.web.server', 'lingmengwork.agent.loop', 'lingmengwork.agent.pool', 'lingmengwork.llm.client', 'lingmengwork.tools.registry', 'lingmengwork.tools.mcp', 'lingmengwork.tui.app', 'lingmengwork.tui.view'],
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
    name='lingmengwork',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='lingmengwork',
)
