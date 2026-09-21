"""One-click Windows launcher using its own venv, independent of the shell PATH."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(exist_ok=True)


def health(url):
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as reply:
            return json.load(reply).get("app") == "framepulse"
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    base = "http://127.0.0.1:47840"
    if not health(base):
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError("请先运行 Setup-FramePulse.cmd 安装运行环境")
        with (RUNTIME / "server.log").open("ab") as out, (RUNTIME / "server-error.log").open("ab") as err:
            process = subprocess.Popen([str(python), "-m", "framepulse", "serve", "--data", str(RUNTIME / "library")],
                                       cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (RUNTIME / "server.pid").write_text(str(process.pid), encoding="ascii")
        for _ in range(40):
            if health(base):
                break
            if process.poll() is not None:
                raise RuntimeError("启动失败，请查看 runtime/server-error.log；端口 47840 可能已被占用")
            time.sleep(.35)
        else:
            raise RuntimeError("服务启动超时，请查看 runtime/server-error.log")
    if not args.no_browser:
        webbrowser.open(base)
    if sys.stdout:
        print(base)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if sys.stderr:
            print(str(error), file=sys.stderr)
        else:
            import tkinter.messagebox
            tkinter.messagebox.showerror("FramePulse", str(error))
        sys.exit(1)
