"""Produce an allowlisted source ZIP, never private media, model files, or credentials."""
from pathlib import Path
import hashlib
import json
import os
import zipfile

root = Path(__file__).resolve().parent.parent
files = []
for directory in ("src/framepulse", "docs", "tests", "scripts", "examples", ".github", "desktop"):
    for current, directories, names in os.walk(root / directory):
        directories[:] = [name for name in directories if name not in {"__pycache__", "node_modules", "dist", "build"}]
        files.extend(Path(current) / name for name in names if Path(name).suffix not in (".pyc", ".pyo"))
for name in ("pyproject.toml", "MANIFEST.in", "LICENSE", "README.md", "CONTRIBUTING.md", "CHANGELOG.md", ".gitignore", "requirements-windows-tested.txt", "Start-FramePulse.cmd", "Setup-FramePulse.cmd", "framepulse.cmd"):
    files.append(root / name)
version = "0.2.0"
destination = root / "dist" / f"FramePulse-Open-{version}-source.zip"
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, "FramePulse-Open/" + path.relative_to(root).as_posix())
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
manifest = {"version": version, "file": destination.name, "bytes": destination.stat().st_size,
            "sha256": digest, "files": len(files), "excludes": ["user media", "analysis library", "models", "credentials", "original handoff", "virtual environments"]}
destination.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest))
