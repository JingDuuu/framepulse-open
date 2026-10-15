"""Model discovery and non-secret preferences; credentials live only in this process."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import urllib.request
import urllib.error
from .protocol import write_document
from .providers import provider_endpoint

PROFILES = {
    "fast": {"label": "快速", "description": "每镜 3 帧，适合大量素材初筛"},
    "balanced": {"label": "均衡", "description": "每镜 5 帧，兼顾覆盖与速度"},
    "detailed": {"label": "细读", "description": "每镜 7 帧，更多画面证据"},
}
LOCAL_OLLAMA = "http://127.0.0.1:11434/v1"


def local_candidates(models):
    """Most recently installed/updated first; this is not a quality ranking."""
    return sorted((m for m in models if m.get("vision") is True and not m.get("remote")),
                  key=lambda m: (m.get("modified_at") or "", m["name"]), reverse=True)


def _json(url, *, body=None, api_key=None, timeout=3):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            raise urllib.error.HTTPError(req.full_url, code, "模型发现不接受重定向", hdrs, fp)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as reply:
        return json.load(reply)


def discover_models(url="http://127.0.0.1:11434/v1", api_key=None):
    parsed, _, native = provider_endpoint(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        if native:
            records = _json(origin + "/api/tags").get("models", [])[:64]
            def describe(record):
                try:
                    info = _json(origin + "/api/show", body={"model": record["name"]})
                except Exception:
                    info = {}
                # /show also establishes whether a harmless-looking alias is remote.
                # If it fails, never infer local execution from /tags alone.
                caps = info.get("capabilities")
                remote = bool(info.get("remote_host") or info.get("remote_model") or
                              record.get("remote_host") or record.get("remote_model") or "cloud" in record["name"].lower())
                return {"name": record["name"], "size": record.get("size"), "capabilities": caps,
                        "vision": "vision" in caps if caps is not None else None,
                        "remote": remote, "modified_at": record.get("modified_at"), "details": record.get("details", {})}
            with ThreadPoolExecutor(max_workers=4) as workers:
                models = list(workers.map(describe, records))
        else:
            base = url.rstrip("/").removesuffix("/chat/completions")
            records = _json(base + "/models", api_key=api_key).get("data", [])[:256]
            models = [{"name": r["id"], "size": None, "capabilities": [], "vision": None} for r in records if isinstance(r.get("id"), str)]
        candidates = local_candidates(models) if native else []
        return {"models": models, "provider_url": url, "errors": [], "reachable": True,
                "suggested_model": candidates[0]["name"] if candidates else ""}
    except Exception:
        return {"models": [], "provider_url": url, "reachable": False, "suggested_model": "",
                "errors": ["无法连接模型服务。请启动 Ollama，稍后点击自动连接；使用其他地址时请选择手动配置。"]}


def provider_defaults() -> dict:
    url, model = os.getenv("FRAMEPULSE_PROVIDER_URL", ""), os.getenv("FRAMEPULSE_MODEL", "")
    if url and model:
        return {"provider_url": url, "model": model, "vision_available": True, "provider_source": "environment"}
    candidates = local_candidates(discover_models()["models"])
    return {"provider_url": LOCAL_OLLAMA, "model": candidates[0]["name"] if candidates else "",
            "vision_available": bool(candidates), "provider_source": "local-discovery" if candidates else "none"}


class Settings:
    def __init__(self, path: Path, defaults: dict):
        self.path, self.lock = path, threading.RLock()
        self.values = {**defaults, "profile": "balanced", "auto_connect": not bool(defaults.get("model")),
                       "vision": bool(defaults.get("vision_available")) and defaults.get("provider_source") == "local-discovery"}
        self.credentials = {}
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.values.update({k: v for k, v in saved.items() if k in {"provider_url", "model", "profile", "vision", "auto_connect"}})
            if "auto_connect" not in saved:
                self.values["auto_connect"] = False
        except (OSError, ValueError, TypeError):
            pass
        if os.getenv("FRAMEPULSE_API_KEY") and defaults.get("provider_url"):
            self.credentials[defaults["provider_url"].rstrip("/")] = os.environ["FRAMEPULSE_API_KEY"]

    def snapshot(self):
        with self.lock:
            return {**self.values, "profiles": PROFILES, "has_api_key": bool(self.key_for(self.values["provider_url"])),
                    "vision_available": bool(self.values.get("model")), "max_video_minutes": 45}

    def key_for(self, url):
        with self.lock:
            return self.credentials.get(url.rstrip("/"))

    def update(self, values):
        with self.lock:
            current = {**self.values, "auto_connect": False, **{k: v for k, v in values.items() if k != "api_key"}}
            if current["provider_url"] != self.values["provider_url"] and "vision" not in values:
                current["vision"] = False
            provider_endpoint(current["provider_url"])
            if current["profile"] not in PROFILES:
                raise ValueError("未知分析模式")
            if not isinstance(current["model"], str) or len(current["model"]) > 256:
                raise ValueError("模型名无效")
            if "api_key" in values:
                key = values["api_key"] or ""
                if len(key) > 8192 or "\n" in key or "\r" in key:
                    raise ValueError("API key 格式无效")
                self.credentials[current["provider_url"].rstrip("/")] = key
            write_document(self.path, {k: current[k] for k in ("provider_url", "model", "profile", "vision", "auto_connect")})
            self.values = current
            return self.snapshot()

    def connect_ollama(self, *, automatic=False):
        # Never replace a saved provider, model, or explicit AI-off preference in the background.
        with self.lock:
            if automatic and (not self.values.get("auto_connect") or self.values.get("model")):
                return {"config": self.snapshot(), "connected": False, "skipped": True}
        result = discover_models(LOCAL_OLLAMA)
        candidates = local_candidates(result["models"])
        with self.lock:
            if automatic and (not self.values.get("auto_connect") or self.values.get("model")):
                return {"config": self.snapshot(), "connected": False, "skipped": True}
            if candidates:
                config = self.update({"provider_url": LOCAL_OLLAMA, "model": candidates[0]["name"], "vision": True})
            else:
                config = self.snapshot()
        return {**result, "config": config, "connected": bool(candidates)}
