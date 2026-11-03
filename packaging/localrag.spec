# PyInstaller onedir build for the LocalRAG desktop launcher.
#
# Keep model files and the user's LocalRAG data directory out of this build.
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).parent
WEB_DATA = ROOT / "src" / "localrag" / "web"

datas = [(str(WEB_DATA), "localrag/web")]
datas += collect_data_files("webview")
tesseract_dir = os.environ.get("LOCALRAG_TESSERACT_DIR")
if tesseract_dir and sys.platform == "win32":
    tesseract_root = Path(tesseract_dir)
    if (tesseract_root / "tesseract.exe").is_file():
        datas.append((str(tesseract_root), "tesseract"))
hiddenimports = [
    "localrag.web",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]
hiddenimports += [
    name for name in collect_submodules(
        "webview",
        filter=lambda name: "webview.platforms.android" not in name,
    )
]

a = Analysis(
    [str(ROOT / "run_desktop.py")],
    pathex=[str(ROOT / "src"), str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="LocalRAG",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
    app = BUNDLE(
        exe,
        name="LocalRAG.app",
        bundle_identifier="com.localrag.desktop",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="LocalRAG",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
    app = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        name="LocalRAG",
    )
