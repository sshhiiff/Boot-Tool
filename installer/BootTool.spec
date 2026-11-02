# PyInstaller recipe: builds the BootTool app folder. Run from the repository root:  pyinstaller installer/BootTool.spec
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.dirname(SPECPATH)            # SPECPATH is the folder holding this file; paths below must not depend on where pyinstaller is run

datas = [(os.path.join(ROOT, "boottool", "static"), "boottool/static")]
hidden = []
for pkg in ("skimage", "scipy", "cv2"):
    datas += collect_data_files(pkg)
    hidden += collect_submodules(pkg)
try:
    datas += collect_data_files("vtracer")
except Exception:
    pass

a = Analysis([os.path.join(SPECPATH, "launcher.py")], pathex=[ROOT], datas=datas, hiddenimports=hidden + ["lxml.etree", "fontTools.ttLib"],
             excludes=["tkinter", "matplotlib", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BootTool", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="BootTool")
