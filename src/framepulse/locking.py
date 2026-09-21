"""Per-content advisory process locks. OS releases them on crash; no stale PID files."""
from contextlib import contextmanager
import os
from pathlib import Path


class AssetBusyError(RuntimeError):
    pass


@contextmanager
def asset_lock(bundle: Path):
    bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    handle = open(bundle / ".write.lock", "a+b")
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            raise AssetBusyError("同一素材正在被另一个分析或修订任务使用，请完成后重试") from error
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
