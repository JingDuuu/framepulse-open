"""Non-secret provider discovery. Never downloads or starts a model."""
import json
import os
import urllib.request


def provider_defaults() -> dict:
    url, model = os.getenv("FRAMEPULSE_PROVIDER_URL", ""), os.getenv("FRAMEPULSE_MODEL", "")
    if url and model:
        return {"provider_url": url, "model": model, "vision_available": True, "provider_source": "environment"}
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as response:
            models = json.load(response).get("models", [])
        candidates = [m for m in models if "vision" in m.get("capabilities", [])]
        if candidates:
            return {"provider_url": "http://127.0.0.1:11434/v1", "model": candidates[0]["name"],
                    "vision_available": True, "provider_source": "local-discovery"}
    except Exception:
        pass
    return {"provider_url": "http://127.0.0.1:11434/v1", "model": "", "vision_available": False, "provider_source": "none"}
