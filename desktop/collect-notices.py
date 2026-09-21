"""Copy dependency notices alongside a local desktop build, without absolute paths."""
from importlib.metadata import distributions
from pathlib import Path
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "build" / "desktop-licenses"
OUTPUT.mkdir(parents=True, exist_ok=True)
inventory = []
build_only = {"pip", "setuptools", "wheel", "packaging", "altgraph", "pefile", "pywin32-ctypes", "pyinstaller-hooks-contrib"}
for distribution in sorted(distributions(), key=lambda item: item.metadata["Name"].lower()):
    name = distribution.metadata["Name"]
    if name.lower() in build_only or name.lower() == "framepulse-open":
        continue
    folder = OUTPUT / name
    copied = []
    for entry in distribution.files or []:
        filename = Path(entry).name
        if not filename.lower().startswith(("license", "copying", "notice", "authors")):
            continue
        source = Path(distribution.locate_file(entry))
        if not source.is_file() or source.suffix.lower() in {".py", ".pyc", ".exe", ".dll", ".pyd"}:
            continue
        folder.mkdir(exist_ok=True)
        destination = folder / (filename if filename not in copied else f"{len(copied)}-{filename}")
        shutil.copyfile(source, destination)
        copied.append(destination.name)
    inventory.append({"name": name, "version": distribution.version, "notices": copied})
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copyfile(python_license, OUTPUT / "Python-LICENSE.txt")
shutil.copyfile(ROOT / "LICENSE", OUTPUT / "FramePulse-LICENSE.txt")
shutil.copyfile(ROOT / "desktop" / "assets" / "GPL-3.0.txt", OUTPUT / "GPL-3.0.txt")
import imageio_ffmpeg
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
result = subprocess.run([ffmpeg, "-L"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
(OUTPUT / "FFmpeg-build-and-license.txt").write_text(result.stderr + "\n" + result.stdout, encoding="utf-8")
(OUTPUT / "dependency-inventory.json").write_text(json.dumps({"python": sys.version.split()[0], "dependencies": inventory}, indent=2), encoding="utf-8")
(OUTPUT / "README.txt").write_text(
    "FramePulse source code is MIT licensed. This local desktop build also contains third-party components under their own licenses.\n"
    "Electron's LICENSE.electron.txt and LICENSES.chromium.html are included in the application directory.\n"
    "FFmpeg is a separate executable invoked as a subprocess; this imageio-ffmpeg wheel contains a GPLv3-or-later build.\n"
    "See FFmpeg-build-and-license.txt for its exact configuration. A public binary distributor must additionally supply the\n"
    "corresponding source under the applicable license; this notice alone is not a source distribution or written offer.\n"
    "Upstream: https://ffmpeg.org/ and https://github.com/imageio/imageio-ffmpeg\n"
    "No language-model weights are included. Installed model licenses remain separate.\n", encoding="utf-8")
