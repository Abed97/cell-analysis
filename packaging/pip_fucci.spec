# PyInstaller spec: builds PIP-FUCCI.app on macOS (and a plain folder app elsewhere, used for tests).
# Run from the repository root:  pyinstaller packaging/pip_fucci.spec --noconfirm
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from pipfucci import __version__  # noqa: E402

hidden = ["pipfucci." + m for m in ("app", "server", "experiment", "pipeline", "segment", "track", "phases",
                                     "analysis", "export", "render", "demo", "io_incucyte", "settings", "updater",
                                     "compare", "macos", "net", "annotate")]
hidden += ["truststore", "certifi", "skimage.measure", "skimage.measure._find_contours"]
for pkg in ("skimage.feature", "skimage.segmentation", "skimage.filters", "skimage.registration", "imagecodecs"):
    hidden += collect_submodules(pkg)
if sys.platform == "darwin":
    hidden += ["webview", "webview.platforms.cocoa", "Foundation", "AppKit", "PyObjCTools.AppHelper",
               "UserNotifications"]
try:  # optional build info written by CI
    import pipfucci._build_info  # noqa: F401
    hidden.append("pipfucci._build_info")
except Exception:
    pass

a = Analysis(
    [os.path.join(ROOT, "run_app.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "pipfucci", "web"), "pipfucci/web")] + collect_data_files("certifi"),
    binaries=collect_dynamic_libs("imagecodecs"),  # LZW and other TIFF compressions from the Incucyte
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cellpose", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PIP-FUCCI",
    console=False,
    argv_emulation=False,
    codesign_identity=None,  # ad-hoc signature, no Apple account needed
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PIP-FUCCI")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PIP-FUCCI.app",
        icon=os.path.join(SPECPATH, "icon.icns"),
        bundle_identifier="org.pipfucci.app",
        version=__version__,
        info_plist={
            # shown as Mitosight (menu bar, Dock, notifications); the bundle and executable keep the
            # internal name PIP-FUCCI so installed copies keep updating themselves
            "CFBundleName": "Mitosight",
            "CFBundleDisplayName": "Mitosight",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.education",
            # the window talks to the app's own server on 127.0.0.1
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSDocumentsFolderUsageDescription": "Mitosight reads Incucyte images and saves results next to them.",
            "NSDesktopFolderUsageDescription": "Mitosight reads Incucyte images and saves results next to them.",
            "NSDownloadsFolderUsageDescription": "Mitosight reads Incucyte images and saves results next to them.",
            "NSRemovableVolumesUsageDescription": "Mitosight reads Incucyte images from external drives.",
            "NSNetworkVolumesUsageDescription": "Mitosight reads Incucyte images from network drives.",
        },
    )
