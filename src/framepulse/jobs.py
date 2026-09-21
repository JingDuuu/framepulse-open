"""Persistent, serial GPU queue shared by the desktop and loopback API."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import threading
import time
import uuid
from .protocol import now_iso, read_document, validate_document, write_document

SUPPORTED = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm", ".m4v", ".mts", ".m2ts", ".mpg", ".mpeg",
             ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif"}


def scan_paths(paths, recursive=True, limit=10000):
    files, skipped, seen = [], [], set()
    for raw in paths:
        source = Path(raw).expanduser().resolve()
        candidates = (source.rglob("*") if recursive else source.glob("*")) if source.is_dir() else [source]
        try:
            for path in candidates:
                if path.is_symlink() or path.is_dir():
                    continue
                if len(files) >= limit:
                    skipped.append({"path": str(source), "reason": f"本次最多加入 {limit} 份素材"})
                    break
                identity = str(path.resolve())
                if identity.casefold() in seen:
                    continue
                seen.add(identity.casefold())
                if not path.is_file():
                    skipped.append({"path": identity, "reason": "文件不存在"})
                elif path.suffix.lower() not in SUPPORTED:
                    if not source.is_dir():
                        skipped.append({"path": identity, "reason": "不支持的媒体格式"})
                else:
                    files.append(path.resolve())
        except OSError:
            skipped.append({"path": str(source), "reason": "目录无法读取"})
    return files, skipped


class JobQueue:
    def __init__(self, root, settings, analyzer=None):
        self.root, self.settings = Path(root), settings
        self.path = self.root / "queue.local.json"
        self.condition = threading.Condition(threading.RLock())
        self.jobs, self.events = {}, {}
        self.state, self.closed, self.worker = "idle", False, None
        self.storage_error = None
        self.analyzer, self._last_saved = analyzer, 0.0
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for job in saved.get("jobs", []):
                if job["state"] in {"running", "queued", "pending"}:
                    job["state"] = "pending"
                    job["progress"] = {"phase": "pending", "current": 0, "total": 1, "message": "已恢复任务，点击开始继续"}
                self.jobs[job["id"]] = job
                self.events[job["id"]] = threading.Event()
            if any(j["state"] == "pending" for j in self.jobs.values()):
                self.state = "paused"
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save(self):
        try:
            write_document(self.path, {"version": 1, "jobs": list(self.jobs.values())})
            self.storage_error = None
        except OSError:
            self.state = "paused"
            self.storage_error = "无法保存队列，已暂停后续任务。请检查素材库所在磁盘的空间和写入权限。"
            raise
        self._last_saved = time.monotonic()

    def _save_safe(self):
        try:
            self._save()
            return True
        except OSError:
            return False

    def snapshot(self):
        with self.condition:
            jobs = [{k: v for k, v in j.items() if k != "request"} for j in self.jobs.values()]
            return copy.deepcopy({"jobs": jobs, "queue": {"state": self.state,
                "active_job_id": next((j["id"] for j in jobs if j["state"] == "running"), None),
                "pending_count": sum(j["state"] == "pending" for j in jobs), "error": self.storage_error}})

    def add(self, paths, options, recursive=True):
        files, skipped = scan_paths(paths, recursive)
        ids = []
        with self.condition:
            if len(self.jobs) + len(files) > 20000:
                raise ValueError("队列历史超过 20000 项，请使用新的素材库")
            active = {j["path"].casefold() for j in self.jobs.values() if j["state"] in {"pending", "running"}}
            for path in files:
                if str(path).casefold() in active:
                    skipped.append({"path": str(path), "reason": "已经在队列中"})
                    continue
                job_id = uuid.uuid4().hex
                request = {**options, "path": str(path)}
                request.pop("api_key", None)
                self.jobs[job_id] = {"id": job_id, "path": str(path), "name": path.name, "state": "pending",
                    "created_at": now_iso(), "profile": options.get("profile", "balanced"), "model": options.get("model", ""),
                    "vision": options.get("vision", False), "request": request,
                    "progress": {"phase": "pending", "current": 0, "total": 1, "message": "等待开始"}}
                self.events[job_id] = threading.Event()
                ids.append(job_id)
                active.add(str(path).casefold())
            try:
                self._save()
            except OSError:
                for job_id in ids:
                    self.jobs.pop(job_id, None)
                    self.events.pop(job_id, None)
                raise
        return {"jobs": [j for j in self.snapshot()["jobs"] if j["id"] in ids], "skipped": skipped}

    def control(self, action):
        with self.condition:
            if action == "pause":
                self.state = "paused"
            elif action in {"start", "resume"}:
                if self.closed:
                    raise ValueError("任务服务已关闭")
                self._save()
                self.state = "running"
                if self.worker is None or not self.worker.is_alive():
                    self.worker = threading.Thread(target=self._work, name="framepulse-queue", daemon=True)
                    self.worker.start()
                self.condition.notify_all()
            else:
                raise ValueError("未知队列操作")
            return self.snapshot()

    def cancel(self, job_id):
        with self.condition:
            job = self.jobs[job_id]
            if job["state"] == "pending":
                job.update(state="cancelled", finished_at=now_iso())
            elif job["state"] == "running":
                self.events[job_id].set()
                job["progress"]["message"] = "正在取消；已完成镜头会保留"
            self._save()

    def retry(self, job_id):
        with self.condition:
            job = self.jobs[job_id]
            if job["state"] not in {"failed", "cancelled"}:
                raise ValueError("只能重试失败或已取消的任务")
            if any(j["id"] != job_id and j["path"] == job["path"] and j["state"] in {"pending", "running"} for j in self.jobs.values()):
                raise ValueError("同一文件已在队列中")
            job.update(state="pending", progress={"phase": "pending", "current": 0, "total": 1, "message": "等待开始；将复用已完成结果"})
            job.pop("error", None)
            job.pop("finished_at", None)
            self.events[job_id].clear()
            self._save()

    def _work(self):
        while True:
            with self.condition:
                if self.closed:
                    return
                job = next((j for j in self.jobs.values() if j["state"] == "pending"), None) if self.state == "running" else None
                if job is None:
                    if self.state == "running":
                        self.state = "idle"
                    self.condition.wait()
                    continue
                job.update(state="running", started_at=now_iso())
                event = self.events[job["id"]]
                if not self._save_safe():
                    job["state"] = "pending"
                    continue
            started = time.monotonic()
            def progress(value):
                with self.condition:
                    job["progress"] = {**value, "elapsed_seconds": round(time.monotonic() - started, 1)}
                    if time.monotonic() - self._last_saved > 1.5:
                        self._save_safe()
            outcome = {}
            try:
                if self.analyzer is None:
                    from .engine import analyze_asset
                    analyze = analyze_asset
                else:
                    analyze = self.analyzer
                options = copy.deepcopy(job["request"])
                source = options.pop("path")
                output = analyze(Path(source), self.root, **options, api_key=self.settings.key_for(options.get("provider_url", "")),
                                 resume=True, progress=progress, cancel=event.is_set)
                document = read_document(output)
                errors = validate_document(document)
                if errors:
                    raise ValueError("记录未通过校验：" + "; ".join(errors[:3]))
                outcome = {"asset_id": Path(output).parent.name, "state": "cancelled" if event.is_set() else "completed"}
                if options.get("vision") and any(s["status"] == "failed" for s in document["shots"]):
                    outcome.update(state="failed", error="部分镜头分析失败；重试会继续未完成镜头")
                if options.get("ocr") and next((r.get("failed_shots", 0) for r in reversed(document["runs"]) if r["kind"] == "ocr"), 0):
                    outcome.update(state="failed", error="部分文字识别失败；已完成数据已保留")
            except Exception as error:
                with self.condition:
                    message = str(error)[:1000]
                    key = self.settings.key_for(job["request"].get("provider_url", ""))
                    if key:
                        message = message.replace(key, "[redacted]")
                    outcome.update(state="cancelled" if event.is_set() else "failed", error=message)
            finally:
                with self.condition:
                    job.update(**outcome, finished_at=now_iso(), elapsed_seconds=round(time.monotonic() - started, 2))
                    self._save_safe()

    def close(self):
        with self.condition:
            self.closed = True
            for job in self.jobs.values():
                if job["state"] == "running":
                    self.events[job["id"]].set()
            self.condition.notify_all()
        if self.worker:
            self.worker.join(timeout=8)
