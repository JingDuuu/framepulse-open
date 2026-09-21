"""Loopback-only review application with explicit write authorization and atomic edits."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import Settings, discover_models, provider_defaults
from .jobs import JobQueue
from .locking import AssetBusyError, asset_lock
from .protocol import (build_overview, claim, export_shots_csv, export_web_annotations, now_iso,
                       read_document, stable_id, validate_document, write_document)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=32768)
    vision: bool = False
    provider_url: str | None = None
    model: str | None = None
    threshold: float = Field(default=27, ge=1, le=100)
    ocr: bool = False
    profile: Literal["fast", "balanced", "detailed"] = "balanced"


class QueueAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(min_length=1, max_length=10000)
    recursive: bool = True
    vision: bool | None = None
    provider_url: str | None = None
    model: str | None = None
    threshold: float = Field(default=27, ge=1, le=100)
    ocr: bool = False
    profile: Literal["fast", "balanced", "detailed"] | None = None


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    vision: bool | None = None
    profile: Literal["fast", "balanced", "detailed"] | None = None


class PickRequest(BaseModel):
    mode: Literal["files", "folder"] = "files"


class ShotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    review: dict | None = None
    analysis: dict | None = None
    emotion: dict | None = None
    regions: list[dict] | None = None


def path_from_uri(uri: str) -> Path:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise HTTPException(400, "只能读取已经登记的本地媒体文件")
    return Path(urllib.request.url2pathname(parsed.path))


def create_app(data_dir: str | Path, *, token: str | None = None) -> FastAPI:
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    web = Path(__file__).parent / "web"
    write_token = token or secrets.token_urlsafe(32)
    defaults = provider_defaults()
    settings = Settings(root / "settings.local.json", defaults)
    queue = JobQueue(root, settings)
    jobs = queue.jobs
    source_fingerprints: dict[str, tuple] = {}
    lock = queue.condition

    @asynccontextmanager
    async def lifespan(app):
        yield
        queue.close()

    app = FastAPI(title="FramePulse local review", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.data_dir = root
    app.state.jobs = jobs
    app.state.queue = queue
    app.state.settings = settings
    app.state.token = write_token

    @app.exception_handler(AssetBusyError)
    async def asset_busy(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse({"detail": "无法读写本地数据，请检查磁盘空间、文件位置和写入权限。"}, status_code=503)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "不接受跨站请求"}, status_code=403)
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            if not secrets.compare_digest(request.headers.get("x-framepulse-token", ""), write_token):
                return JSONResponse({"detail": "会话已失效，请刷新界面"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def bundle(asset_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", asset_id):
            raise HTTPException(404, "素材不存在")
        directory = (root / asset_id).resolve()
        if directory.parent != root:
            raise HTTPException(404, "素材不存在")
        return directory

    def document(asset_id: str) -> dict:
        try:
            return read_document(bundle(asset_id) / "record.fmp.json")
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "素材记录不存在或尚未完成")

    @app.get("/api/health")
    def health():
        from .engine import capabilities
        return {"version": __version__, "app": "framepulse", "capabilities": capabilities()}

    @app.get("/api/config")
    def config():
        return {"token": write_token, **settings.snapshot()}

    @app.post("/api/config")
    def update_config(request: ConfigRequest):
        try:
            values = request.model_dump(exclude_unset=True)
            if any(v is None for k, v in values.items() if k != "api_key"):
                raise ValueError("配置字段不能为空")
            return {"token": write_token, **settings.update(values)}
        except ValueError as error:
            raise HTTPException(422, str(error))

    @app.get("/api/models")
    def models(provider_url: str | None = None):
        url = provider_url or settings.snapshot()["provider_url"]
        try:
            return discover_models(url, settings.key_for(url))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @app.get("/api/assets")
    def list_assets():
        assets = []
        for file in root.glob("*/record.fmp.json"):
            try:
                doc = read_document(file)
                asset_id = file.parent.name
                shots = doc["shots"]
                frame = next((k for s in shots for k in s["keyframes"]), None)
                assets.append({"id": asset_id, "name": doc["asset"]["name"], "kind": doc["asset"]["kind"],
                               "shot_count": len(shots), "duration_us": doc["asset"]["technical"].get("duration_us"),
                               "analyzed_count": sum(s["status"] == "analyzed" for s in shots),
                               "reviewed_count": sum(s["review"]["state"] == "accepted" for s in shots),
                               "thumbnail": f"/api/assets/{asset_id}/files/{frame['uri']}" if frame else None,
                               "updated_at": doc["updated_at"], "warnings": doc.get("warnings", []), "overview": doc.get("overview"),
                               "status": "analyzed" if all(s["status"] == "analyzed" for s in shots) else "measured"})
            except (OSError, ValueError, KeyError):
                continue
        return {"assets": sorted(assets, key=lambda a: a["updated_at"], reverse=True)}

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str):
        return document(asset_id)

    @app.get("/api/assets/{asset_id}/media")
    def media(asset_id: str):
        doc = document(asset_id)
        folder = bundle(asset_id)
        if doc["asset"]["kind"] == "image":
            source = (folder / doc["shots"][0]["keyframes"][0]["uri"]).resolve()
            if not source.is_relative_to(folder):
                raise HTTPException(400, "非法媒体路径")
        else:
            source = folder / "preview.mp4"
            if not source.exists():
                source = path_from_uri(doc["asset"]["source"]["uri"])
        if not source.is_file():
            raise HTTPException(404, "原始媒体已移动；请从新路径再次分析，内容哈希会关联已有记录")
        if doc["asset"]["kind"] == "video" and source != folder / "preview.mp4":
            stat = source.stat()
            fingerprint = (str(source), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if source_fingerprints.get(asset_id) != fingerprint:
                digest = hashlib.sha256()
                with source.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != doc["asset"]["source"]["sha256"]:
                    raise HTTPException(409, "此路径的原文件已经变化，请重新分析；旧标记不能应用到新媒体")
                source_fingerprints[asset_id] = fingerprint
        return FileResponse(source)

    @app.get("/api/assets/{asset_id}/files/{relative:path}")
    def evidence_file(asset_id: str, relative: str):
        folder = bundle(asset_id)
        target = (folder / relative).resolve()
        if not target.is_relative_to(folder / "frames") or target.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or not target.is_file():
            raise HTTPException(404, "证据帧不存在")
        return FileResponse(target)

    def request_options(request):
        config = settings.snapshot()
        values = request.model_dump(exclude={"paths", "recursive", "path"})
        values["provider_url"] = values.get("provider_url") or config["provider_url"]
        values["model"] = values.get("model") or config["model"]
        values["profile"] = values.get("profile") or config["profile"]
        if values.get("vision") is None:
            values["vision"] = bool(config.get("vision")) and values["provider_url"] == config["provider_url"]
        if values["vision"]:
            from .providers import provider_endpoint
            try:
                provider_endpoint(values["provider_url"])
            except ValueError as error:
                raise HTTPException(422, str(error))
            if not values["model"]:
                raise HTTPException(422, "请先选择视觉模型")
        return values

    @app.post("/api/queue/add")
    def add_queue(request: QueueAddRequest):
        try:
            return queue.add(request.paths, request_options(request), request.recursive)
        except ValueError as error:
            raise HTTPException(422, str(error))

    @app.post("/api/queue/{action}")
    def control_queue(action: str):
        try:
            return queue.control(action)
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.post("/api/analyze")
    def analyze(request: AnalyzeRequest):
        source = Path(request.path).expanduser().resolve()
        if not source.is_file():
            raise HTTPException(400, "找不到这个文件，请选择本机视频或图片")
        result = queue.add([str(source)], request_options(request))
        if not result["jobs"]:
            raise HTTPException(409, result["skipped"][0]["reason"] if result["skipped"] else "没有可分析素材")
        queue.control("start")
        return {"id": result["jobs"][0]["id"]}

    @app.get("/api/jobs")
    def list_jobs():
        return queue.snapshot()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        try:
            queue.cancel(job_id)
            return {"ok": True}
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        try:
            queue.retry(job_id)
            return {"ok": True}
        except KeyError:
            raise HTTPException(404, "任务不存在")
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/pick")
    def pick(request: PickRequest | None = None):
        try:
            import tkinter as tk
            from tkinter import filedialog
            window = tk.Tk()
            window.withdraw()
            window.attributes("-topmost", True)
            try:
                if request and request.mode == "folder":
                    folder = filedialog.askdirectory(title="选择素材文件夹")
                    paths = [folder] if folder else []
                else:
                    paths = list(filedialog.askopenfilenames(title="选择视频或图片", filetypes=[("视频和图片", "*.mp4 *.mov *.mkv *.avi *.mxf *.webm *.m4v *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("所有文件", "*.*")]))
            finally:
                window.destroy()
            return {"paths": paths, "path": paths[0] if paths else None}
        except Exception:
            raise HTTPException(503, "文件选择器不可用，请直接粘贴文件的完整路径")

    @app.patch("/api/assets/{asset_id}/shots/{shot_id}")
    def patch_shot(asset_id: str, shot_id: str, patch: ShotPatch):
        with lock, asset_lock(bundle(asset_id)):
            doc = document(asset_id)
            source = str(path_from_uri(doc["asset"]["source"]["uri"]).resolve())
            if any(j["path"] == source and j["state"] in ("pending", "queued", "running") for j in jobs.values()):
                raise HTTPException(409, "素材仍在分析，完成后即可修订")
            if patch.revision != doc["revision"]:
                raise HTTPException(409, "记录已更新，请重新载入后再保存")
            shot = next((s for s in doc["shots"] if s["id"] == shot_id), None)
            if shot is None:
                raise HTTPException(404, "镜头不存在")
            before = copy.deepcopy(shot)
            if patch.review is not None:
                if set(patch.review) - {"state", "note"}:
                    raise HTTPException(422, "未知审核字段")
                shot["review"].update(patch.review)
            for group in ("analysis", "emotion"):
                values = getattr(patch, group)
                if values is None:
                    continue
                if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in values):
                    raise HTTPException(422, "字段名无效")
                if group == "emotion" and set(values) - {"valence", "arousal", "tension"}:
                    raise HTTPException(422, "未知情绪维度")
                for field, value in values.items():
                    evidence = shot[group].get(field, {}).get("evidence_ids", [])
                    shot[group][field] = {**claim(value, "human", evidence_ids=evidence), "review_status": "edited"}
                shot["review"]["state"] = "edited"
            if patch.regions is not None:
                shot["regions"] = [{**r, "id": r.get("id") or stable_id(shot_id, "region", uuid.uuid4().hex),
                                    "origin": "human", "confidence": None} for r in patch.regions]
                shot["review"]["state"] = "edited"
            if patch.review and patch.review.get("state") in ("accepted", "rejected"):
                shot["review"]["state"] = patch.review["state"]
                for group in ("analysis", "emotion"):
                    for item in shot[group].values():
                        item["review_status"] = patch.review["state"]
            doc["overview"] = build_overview(doc)
            errors = validate_document(doc)
            if errors:
                raise HTTPException(422, "；".join(errors[:5]))
            doc["history"].append({"at": now_iso(), "action": "shot.review", "shot_id": shot_id,
                                   "revision": doc["revision"], "before": before})
            doc["revision"] += 1
            doc["updated_at"] = now_iso()
            write_document(bundle(asset_id) / "record.fmp.json", doc)
            return doc

    @app.get("/api/assets/{asset_id}/export")
    def export(asset_id: str, format: str = "json"):
        doc = document(asset_id)
        if format == "json":
            data, mime, extension = json.dumps(doc, ensure_ascii=False, indent=2), "application/json", "fmp.json"
        elif format == "csv":
            data, mime, extension = export_shots_csv(doc), "text/csv; charset=utf-8", "shots.csv"
        elif format == "web-annotation":
            data, mime, extension = json.dumps(export_web_annotations(doc, evidence_base_uri=bundle(asset_id).as_uri() + "/"), ensure_ascii=False, indent=2), "application/ld+json", "annotations.jsonld"
        else:
            raise HTTPException(400, "支持 json、csv、web-annotation")
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="framepulse-{asset_id[:12]}.{extension}"'})

    @app.get("/api/protocol")
    def protocol():
        return {"name": "FramePulse Media Protocol", "version": "0.1.0", "status": "experimental",
                "schema": read_document(Path(__file__).parent / "schemas" / "fmp-0.1.schema.json")}

    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
