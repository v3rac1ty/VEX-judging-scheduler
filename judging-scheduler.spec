# PyInstaller spec: one-file Windows build. Run build_exe.bat instead of calling this directly.
# Output: dist\VEX-Judging-Scheduler.exe
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")  # uvicorn picks its loop/protocol modules at runtime
    + collect_submodules("judging")
)
datas = [("static", "static")] + collect_data_files("tzdata")  # page files + timezone names

a = Analysis(
    ["launcher.py"],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="VEX-Judging-Scheduler",
    console=True,  # the window shows the address and admin PIN; closing it stops the server
    upx=False,
)
