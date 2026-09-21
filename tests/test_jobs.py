import json
import threading
import time

from framepulse.config import Settings, discover_models
from framepulse.jobs import JobQueue, scan_paths
from framepulse.protocol import write_document


def until(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("queue condition did not become true")


def settings(root):
    return Settings(root / "settings.local.json", {"provider_url": "http://127.0.0.1:11434/v1", "model": "test", "vision_available": True, "provider_source": "local-discovery"})


def test_folder_scan_deduplicates_and_skips_non_media(tmp_path):
    nested = tmp_path / "中文 空格"
    nested.mkdir()
    media = nested / "one.mp4"
    media.write_bytes(b"video")
    (nested / "notes.txt").write_text("ignore")
    files, skipped = scan_paths([str(tmp_path), str(media)])
    assert files == [media.resolve()]
    assert skipped == []


def test_queue_waits_for_start_and_pause_finishes_current(tmp_path, document):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    entered, release = threading.Event(), threading.Event()
    calls = []
    output = tmp_path / "library" / "asset" / "record.fmp.json"
    write_document(output, document)
    def analyze(source, root, **kwargs):
        calls.append(source.name)
        entered.set()
        release.wait(3)
        return output
    queue = JobQueue(tmp_path / "library", settings(tmp_path), analyze)
    try:
        added = queue.add([str(first), str(second)], {"vision": False, "profile": "fast"})
        assert len(added["jobs"]) == 2 and not calls
        queue.control("start")
        assert entered.wait(2)
        queue.control("pause")
        release.set()
        until(lambda: queue.snapshot()["jobs"][0]["state"] == "completed")
        assert calls == ["one.jpg"]
        assert queue.snapshot()["jobs"][1]["state"] == "pending"
        queue.control("resume")
        until(lambda: all(j["state"] == "completed" for j in queue.snapshot()["jobs"]))
        assert calls == ["one.jpg", "two.jpg"]
    finally:
        release.set()
        queue.close()


def test_restore_does_not_autostart_and_retry_is_explicit(tmp_path):
    media = tmp_path / "one.mp4"
    media.write_bytes(b"x")
    root = tmp_path / "library"
    queue = JobQueue(root, settings(tmp_path))
    job = queue.add([str(media)], {"vision": False})["jobs"][0]
    restored = JobQueue(root, settings(tmp_path))
    assert restored.snapshot()["queue"]["state"] == "paused"
    assert restored.snapshot()["jobs"][0]["state"] == "pending"
    restored.cancel(job["id"])
    assert restored.snapshot()["jobs"][0]["state"] == "cancelled"
    restored.retry(job["id"])
    assert restored.snapshot()["jobs"][0]["state"] == "pending"
    restored.close()


def test_failure_does_not_block_next_file(tmp_path, document):
    sources = [tmp_path / "broken.mp4", tmp_path / "good.mp4"]
    for path in sources:
        path.write_bytes(b"x")
    output = tmp_path / "library" / "asset" / "record.fmp.json"
    write_document(output, document)
    def analyze(source, root, **kwargs):
        if source.name.startswith("broken"):
            raise ValueError("unreadable")
        return output
    queue = JobQueue(tmp_path / "library", settings(tmp_path), analyze)
    try:
        queue.add([str(p) for p in sources], {"vision": False})
        queue.control("start")
        until(lambda: [j["state"] for j in queue.snapshot()["jobs"]] == ["failed", "completed"])
    finally:
        queue.close()


def test_credentials_never_persist_and_do_not_follow_other_endpoint(tmp_path):
    config = settings(tmp_path)
    config.update({"api_key": "secret-demo-key", "profile": "fast"})
    assert config.snapshot()["has_api_key"] is True
    assert "secret-demo-key" not in config.path.read_text()
    config.update({"provider_url": "https://example.invalid/v1"})
    assert config.key_for("https://example.invalid/v1") is None
    assert config.snapshot()["vision"] is False
    assert Settings(config.path, {"provider_url": "", "model": ""}).snapshot()["has_api_key"] is False


def test_model_discovery_uses_show_when_tags_lacks_capabilities(monkeypatch):
    calls = []
    def respond(url, **kwargs):
        calls.append(url)
        return {"models": [{"name": "vision-model", "size": 123}]} if url.endswith("tags") else {"capabilities": ["vision", "completion"]}
    monkeypatch.setattr("framepulse.config._json", respond)
    models = discover_models()["models"]
    assert models[0]["vision"] is True
    assert any(url.endswith("show") for url in calls)


def test_model_discovery_never_forwards_key_across_http_redirect():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    source_headers, destination_headers = [], []

    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            destination_headers.append(self.headers.get("Authorization"))
            body = json.dumps({"data": [{"id": "synthetic-vision-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            source_headers.append(self.headers.get("Authorization"))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/models")
            self.end_headers()

        def log_message(self, *args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
               for server in (source, destination)]
    for worker in threads:
        worker.start()
    try:
        response = discover_models(f"http://127.0.0.1:{source.server_port}/v1", api_key="synthetic-redirect-test-key")
        assert source_headers == ["Bearer synthetic-redirect-test-key"]
        assert destination_headers == []
        assert response["models"] == [] and response["errors"]
        assert "synthetic-redirect-test-key" not in json.dumps(response)
    finally:
        for server in (source, destination):
            server.shutdown()
            server.server_close()
        for worker in threads:
            worker.join(timeout=2)


def test_environment_key_stays_bound_to_defaults_when_saved_provider_differs(tmp_path, monkeypatch):
    configured = tmp_path / "settings.local.json"
    configured.write_text(json.dumps({"provider_url": "https://provider-b.invalid/v1", "model": "saved-b",
                                      "profile": "detailed", "vision": True}), encoding="utf-8")
    monkeypatch.setenv("FRAMEPULSE_API_KEY", "synthetic-environment-test-key")
    config = Settings(configured, {"provider_url": "https://provider-a.invalid/v1", "model": "environment-a",
                                   "vision_available": True, "provider_source": "environment"})
    assert config.snapshot()["provider_url"] == "https://provider-b.invalid/v1"
    assert config.snapshot()["profile"] == "detailed"
    assert config.key_for("https://provider-a.invalid/v1") == "synthetic-environment-test-key"
    assert config.key_for("https://provider-b.invalid/v1") is None
    assert config.snapshot()["has_api_key"] is False
    assert "synthetic-environment-test-key" not in configured.read_text(encoding="utf-8")


def test_queue_initial_storage_failure_pauses_and_keeps_job_pending(tmp_path, document, monkeypatch):
    import framepulse.jobs as jobs_module

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"synthetic")
    output = tmp_path / "library" / "asset" / "record.fmp.json"
    write_document(output, document)
    calls = []

    def analyze(source, root, **kwargs):
        calls.append(source.name)
        return output

    queue = JobQueue(tmp_path / "library", settings(tmp_path), analyze)
    queue.add([str(media)], {"vision": False})
    original_write = jobs_module.write_document
    storage_failed = {"enabled": True}

    def write_with_fault(path, value):
        # Let control() persist pending jobs, then fail the worker's first write.
        if path == queue.path and storage_failed["enabled"] and any(job["state"] == "running" for job in value["jobs"]):
            raise OSError("synthetic disk full")
        return original_write(path, value)

    monkeypatch.setattr(jobs_module, "write_document", write_with_fault)
    try:
        queue.control("start")
        until(lambda: queue.snapshot()["queue"]["error"] is not None)
        snapshot = queue.snapshot()
        assert snapshot["queue"]["state"] == "paused"
        assert snapshot["jobs"][0]["state"] == "pending"
        assert queue.worker.is_alive() and calls == []
        storage_failed["enabled"] = False
        queue.control("resume")
        until(lambda: queue.snapshot()["jobs"][0]["state"] == "completed")
        assert queue.snapshot()["queue"]["error"] is None
        assert calls == ["clip.mp4"]
        assert json.loads(queue.path.read_text(encoding="utf-8"))["jobs"][0]["state"] == "completed"
    finally:
        storage_failed["enabled"] = False
        queue.close()


def test_queue_final_storage_failure_does_not_kill_worker_or_start_next_job(tmp_path, document, monkeypatch):
    import framepulse.jobs as jobs_module

    media = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    for item in media:
        item.write_bytes(b"synthetic")
    output = tmp_path / "library" / "asset" / "record.fmp.json"
    write_document(output, document)
    calls, fail_final = [], {"enabled": False}

    def analyze(source, root, **kwargs):
        calls.append(source.name)
        if source.name == "first.mp4":
            fail_final["enabled"] = True
        return output

    queue = JobQueue(tmp_path / "library", settings(tmp_path), analyze)
    queue.add([str(item) for item in media], {"vision": False})
    original_write = jobs_module.write_document

    def write_with_fault(path, value):
        if path == queue.path and fail_final["enabled"]:
            raise OSError("synthetic disk full")
        return original_write(path, value)

    monkeypatch.setattr(jobs_module, "write_document", write_with_fault)
    try:
        queue.control("start")
        until(lambda: queue.snapshot()["queue"]["error"] is not None)
        snapshot = queue.snapshot()
        assert snapshot["queue"]["state"] == "paused"
        assert [item["state"] for item in snapshot["jobs"]] == ["completed", "pending"]
        assert queue.worker.is_alive() and calls == ["first.mp4"]
        fail_final["enabled"] = False
        queue.control("resume")
        until(lambda: all(item["state"] == "completed" for item in queue.snapshot()["jobs"]))
        assert queue.snapshot()["queue"]["error"] is None
        assert calls == ["first.mp4", "second.mp4"]
    finally:
        fail_final["enabled"] = False
        queue.close()


def test_queue_add_storage_failure_rolls_back_new_jobs(tmp_path, monkeypatch):
    import pytest
    import framepulse.jobs as jobs_module

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"synthetic")
    queue = JobQueue(tmp_path / "library", settings(tmp_path))

    def no_space(*args, **kwargs):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(jobs_module, "write_document", no_space)
    try:
        with pytest.raises(OSError):
            queue.add([str(media)], {"vision": False})
        snapshot = queue.snapshot()
        assert snapshot["jobs"] == [] and queue.events == {}
        assert snapshot["queue"]["state"] == "paused" and snapshot["queue"]["error"]
    finally:
        queue.close()


def test_resume_retries_terminal_storage_failure_without_pending_jobs(tmp_path, document, monkeypatch):
    import pytest
    import framepulse.jobs as jobs_module

    media = tmp_path / "last.mp4"
    media.write_bytes(b"synthetic")
    output = tmp_path / "library" / "asset" / "record.fmp.json"
    write_document(output, document)
    calls, fail_final, attempted_states = [], {"enabled": False}, []

    def analyze(source, root, **kwargs):
        calls.append(source.name)
        fail_final["enabled"] = True
        return output

    queue = JobQueue(tmp_path / "library", settings(tmp_path), analyze)
    queue.add([str(media)], {"vision": False})
    original_write = jobs_module.write_document

    def write_with_fault(path, value):
        if path == queue.path:
            attempted_states.append([job["state"] for job in value["jobs"]])
            if fail_final["enabled"]:
                raise OSError("synthetic disk full")
        return original_write(path, value)

    monkeypatch.setattr(jobs_module, "write_document", write_with_fault)
    try:
        queue.control("start")
        until(lambda: queue.snapshot()["queue"]["error"] is not None)
        snapshot = queue.snapshot()
        assert snapshot["queue"]["state"] == "paused"
        assert snapshot["queue"]["pending_count"] == 0
        assert snapshot["queue"]["active_job_id"] is None
        assert snapshot["jobs"][0]["state"] == "completed"
        assert json.loads(queue.path.read_text(encoding="utf-8"))["jobs"][0]["state"] == "running"
        worker = queue.worker
        assert worker.is_alive()

        attempts_before_retry = len(attempted_states)
        with pytest.raises(OSError, match="synthetic disk full"):
            queue.control("resume")
        assert len(attempted_states) == attempts_before_retry + 1
        assert attempted_states[-1] == ["completed"]
        assert queue.snapshot()["queue"]["state"] == "paused"
        assert queue.snapshot()["queue"]["error"] is not None

        fail_final["enabled"] = False
        resumed = queue.control("resume")
        assert resumed["queue"]["error"] is None
        assert resumed["queue"]["pending_count"] == 0
        assert len(attempted_states) == attempts_before_retry + 2
        assert attempted_states[-1] == ["completed"]
        assert json.loads(queue.path.read_text(encoding="utf-8"))["jobs"][0]["state"] == "completed"
        until(lambda: queue.snapshot()["queue"]["state"] == "idle")
        assert queue.worker is worker and worker.is_alive()
        assert calls == ["last.mp4"]

        restored = JobQueue(queue.root, settings(tmp_path))
        try:
            assert restored.snapshot()["jobs"][0]["state"] == "completed"
            assert restored.snapshot()["queue"]["pending_count"] == 0
            assert restored.snapshot()["queue"]["state"] == "idle"
        finally:
            restored.close()
    finally:
        fail_final["enabled"] = False
        queue.close()
