# Build only from the isolated .qa-venv. Models are deliberately not bundled.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = Path(SPECPATH).parent
ffmpeg_data, ffmpeg_binaries, ffmpeg_hidden = collect_all('imageio_ffmpeg')
a = Analysis(
    [str(root / 'scripts' / 'desktop-backend.py')],
    pathex=[str(root / 'src')],
    binaries=ffmpeg_binaries,
    datas=collect_data_files('framepulse') + ffmpeg_data,
    hiddenimports=ffmpeg_hidden + collect_submodules('uvicorn'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'tensorflow', 'matplotlib', 'scipy', 'IPython'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='framepulse-backend',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, icon=str(root / 'desktop' / 'assets' / 'icon.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='framepulse-backend')
