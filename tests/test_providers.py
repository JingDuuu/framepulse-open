"""Provider output is untrusted: verify limits, evidence, and network controls."""
import json
import base64
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from framepulse import providers
from framepulse.media import AnalysisCancelled


def test_untrusted_unknown_fields_confidence_and_regions():
    raw = {"analysis": {"summary": {"value": "画面中的可见物体", "confidence": float("nan"), "evidence_ids": ["invented"]},
                         "shot_size": {"value": "中景", "confidence": 40}, "subjects": ["杯子", {"cmd": "do-not-run"}],
                         "shell_command": "do-not-run"},
           "emotion": {"valence": {"value": -0.5}, "arousal": {"value": True}, "tension": {"value": 0.4, "confidence": 0.7}},
           "regions": [{"keyframe_id": "frame1", "xywh": [0.8, 0.8, 0.4, 0.4], "label": "bad"},
                       {"keyframe_id": "invented", "xywh": [0, 0, 1, 1], "label": "bad"},
                       {"keyframe_id": "frame1", "xywh": [0.1, 0.2, 0.3, 0.4], "label": "杯子", "confidence": 0.8}]}
    result = providers.normalize_result(raw, ["frame1"])
    assert "shell_command" not in result["analysis"]
    assert result["analysis"]["summary"]["confidence"] is None
    assert result["analysis"]["shot_size"]["confidence"] is None
    assert result["analysis"]["summary"]["evidence_ids"] == ["frame1"]
    assert result["analysis"]["subjects"]["value"] == ["杯子"]
    assert result["emotion"]["valence"]["value"] is None
    assert result["emotion"]["arousal"]["value"] is None
    assert result["emotion"]["tension"]["value"] == 0.4
    assert len(result["regions"]) == 1
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:secret@localhost/v1", "https://example.com/v1?api_key=secret", "https://example.com/#secret"])
def test_provider_url_no_embedded_credentials_or_local_file(url):
    with pytest.raises(ValueError):
        providers.provider_endpoint(url)


def test_ollama_native_thinking_control_and_generic_base_path():
    assert providers.provider_endpoint("http://127.0.0.1:11434/v1")[1:] == ("/api/chat", True)
    assert providers.provider_endpoint("https://example.com/custom/v1")[1:] == ("/custom/v1/chat/completions", False)


@pytest.mark.parametrize("content", ["not json", '{"analysis":', '{"tool_call":"execute something"}'])
def test_invalid_model_response_is_never_success(content):
    with pytest.raises(providers.ProviderError):
        providers.normalize_result(providers.parse_model_json(content), ["frame1"])


def test_complete_explicit_abstention_is_valid_and_not_invented_emotion():
    raw = {"analysis": {field: None for field in providers.ANALYSIS_FIELDS}, "emotion": {"valence": 0.5, "arousal": 0.2}}
    result = providers.normalize_result(raw, ["frame1"])
    assert result["abstained"] is True
    assert all(item["value"] is None for item in result["emotion"].values())
    assert result["warnings"]


class SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.received.set()
        time.sleep(0.8)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
    def log_message(self, *args):
        pass


def test_inflight_http_cancel_returns_promptly():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    server.received = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cancelled = threading.Event()
    def trigger():
        server.received.wait(2)
        cancelled.set()
    threading.Thread(target=trigger, daemon=True).start()
    started = time.monotonic()
    try:
        with pytest.raises(AnalysisCancelled):
            providers._post_json(f"http://127.0.0.1:{server.server_port}/v1", {}, cancel=cancelled.is_set, timeout=3)
        assert time.monotonic() - started < 0.7
    finally:
        server.shutdown()
        server.server_close()


def test_native_request_short_ids_and_retry_real_json_only(tmp_path, monkeypatch):
    (tmp_path / "frames").mkdir()
    Image.new("RGB", (30, 20), "red").save(tmp_path / "frames/test.jpg")
    shot = {"range": {"start_us": 0, "end_us": 1_000_000}, "keyframes": [{"id": "stable-real-frame-uuid", "time_us": 100_000, "uri": "frames/test.jpg"}]}
    requests = []
    def post(url, body, *args, **kwargs):
        requests.append(json.loads(json.dumps(body)))
        if len(requests) == 1:
            return {"message": {"content": '{"analysis":'}}
        return {"message": {"content": json.dumps({"analysis": {"summary": {"value": "红色图形", "evidence_ids": ["K1"]}}, "emotion": {}})}}
    monkeypatch.setattr(providers, "_post_json", post)
    result = providers.analyze_shot(shot, tmp_path, "http://127.0.0.1:11434/v1", "fixture-model")
    assert len(requests) == 2
    assert requests[0]["think"] is False
    assert isinstance(requests[0]["format"], dict)
    assert "stable-real-frame-uuid" not in json.dumps(requests[0])
    assert result["analysis"]["summary"]["evidence_ids"] == ["stable-real-frame-uuid"]
    assert result["warnings"]


def test_profile_bounds_images_and_regions_remain_keyframe_only(tmp_path, monkeypatch):
    (tmp_path / "frames").mkdir()
    Image.new("RGB", (1600, 800), "blue").save(tmp_path / "frames/large.jpg")
    shot = {"range": {"start_us": 0, "end_us": 1_000_000}, "keyframes": [
        {"id": f"frame-{i}", "time_us": i * 100_000, "uri": "frames/large.jpg"} for i in range(7)]}
    requests = []
    def post(url, body, *args, **kwargs):
        requests.append(body)
        return {"done_reason": "stop", "load_duration": 500_000_000, "prompt_eval_duration": 1_500_000_000, "eval_duration": 2_000_000_000, "total_duration": 4_000_000_000, "eval_count": 400,
                "message": {"content": json.dumps({"analysis": {"summary": "蓝色物体", "visual_medium": "3d_animation", "themes": ["城市"]},
                 "regions": [{"keyframe_id": "K2", "xywh": [0.1, 0.2, 0.4, 0.5], "label": "主体", "confidence": None}]})}}
    monkeypatch.setattr(providers, "_post_json", post)
    result = providers.analyze_shot(shot, tmp_path, "http://127.0.0.1:11434/v1", "fixture", max_frames=3, image_max_side=768, context_limit=8192)
    images = requests[0]["messages"][-1]["images"]
    assert len(images) == 3
    assert Image.open(io.BytesIO(base64.b64decode(images[0]))).size == (768, 384)
    assert requests[0]["options"]["num_ctx"] == 8192
    assert result["analysis"]["visual_medium"]["value"] == "3d_animation"
    assert result["regions"][0]["keyframe_id"] == "frame-1"
    assert result["regions"][0]["extensions"] == {"source": "vision", "tracking": False}
    assert "time_range" not in result["regions"][0]
    assert result["usage"]["load_seconds"] == 0.5
    assert result["usage"]["prompt_eval_seconds"] == 1.5
    assert result["usage"]["eval_seconds"] == 2
    assert result["usage"]["total_seconds"] == 4
    assert result["usage"]["generation_tokens_per_second"] == 200


def test_unsupported_visual_medium_does_not_get_invented_label():
    raw = {"analysis": {"summary": "测试", "visual_medium": {"value": {"guess": "CG"}}}}
    result = providers.normalize_result(raw, ["frame-1"])
    assert result["analysis"]["visual_medium"]["value"] is None
    assert result["warnings"]
