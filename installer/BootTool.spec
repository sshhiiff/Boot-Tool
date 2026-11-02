# PyInstaller recipe: builds the BootTool app folder. Run from the repository root:  pyinstaller installer/BootTool.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("boottool/static", "boottool/static")]
hidden = []
for pkg in ("skimage", "scipy", "cv2"):
    datas += collect_data_files(pkg)
    hidden += collect_submodules(pkg)
try:
    datas += collect_data_files("vtracer")
except Exception:
    pass

a = Analysis(["installer/launcher.py"], pathex=["."], datas=datas, hiddenimports=hidden + ["lxml.etree", "fontTools.ttLib"],
             excludes=["tkinter", "matplotlib", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BootTool", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="BootTool")
