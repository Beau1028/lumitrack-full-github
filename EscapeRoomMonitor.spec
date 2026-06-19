# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_submodules


streamlit_datas, streamlit_binaries, streamlit_hidden = collect_all("streamlit")
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")
try:
    reportlab_datas, reportlab_binaries, reportlab_hidden = collect_all("reportlab")
except Exception:
    reportlab_datas, reportlab_binaries, reportlab_hidden = [], [], []
streamlit_datas = [
    item for item in streamlit_datas if "streamlit\\.agents" not in item[1]
]

datas = [
    ("app.py", "."),
    ("stores.yaml", "."),
    ("store_locations.yaml", "."),
    ("manual_estimates.yaml", "."),
    ("stores.sample.yaml", "."),
    ("sample_booking.html", "."),
    ("assets/sample_escape_room.db", "assets"),
    ("assets/lumitrack-logo.svg", "assets"),
    *streamlit_datas,
    *playwright_datas,
    *reportlab_datas,
]

binaries = [
    *streamlit_binaries,
    *playwright_binaries,
    *reportlab_binaries,
]

hiddenimports = [
    *streamlit_hidden,
    *playwright_hidden,
    *reportlab_hidden,
    *collect_submodules("scraper"),
    "scraper.analytics",
    "streamlit.web.bootstrap",
]

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name="EscapeRoomMonitor",
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
    name="EscapeRoomMonitor",
)
