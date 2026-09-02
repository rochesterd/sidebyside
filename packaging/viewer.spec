# PyInstaller spec for viewer.py -- see PACKAGING.md and app.spec's comment
# for the shared reasoning (same empirically-confirmed dependency set for
# PySide6/cv2/av, same upx=False antivirus rationale).
#
# This is the *only* spec built for the viewer-only installer
# (packaging/sidebyside-viewer.iss), which ships to machines with no
# cameras, no IDS peak SDK and usually no admin rights -- see ROADMAP.md's
# "Phase 4: two installers" entry.
#
# The clinic installer deliberately does NOT ship this exe: app.exe already
# contains the viewer (app.py imports viewer.py, so Watch and Past
# recordings work from inside the kiosk), and bundling a second full
# PySide6+cv2+av tree would add hundreds of MB to an installer that's
# already ~490MB for no capability the clinic machine lacks.
#
# `excludes` below is an assertion, not a size optimisation. viewer.py's
# import graph -- viewer -> session_reader/session_export -> recorder
# (constants only via session_format) -> av, camera -> plus compositor,
# qt_image, config -- reaches nothing camera-facing. Naming those modules
# here means that stops being something to re-check by eye: if a future
# edit makes the viewer import ids_camera or uvc_enumeration, this build
# fails loudly instead of silently gaining a 300MB SDK dependency the
# review machine can't satisfy.

a = Analysis(
    ['../viewer.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['ids_peak', 'ids_peak_ipl', 'pygrabber', 'comtypes'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    upx=False,
    upx_exclude=[],
    name='viewer',
)
