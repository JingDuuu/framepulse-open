from pathlib import Path
import hashlib

import pytest
from fastapi.testclient import TestClient

from framepulse.protocol import read_document, write_document
from framepulse.server import create_app


@pytest.fixture
def client(tmp_path, document, monkeypatch):
    monkeypatch.setattr("framepulse.server.provider_defaults", lambda: {"provider_url": "", "model": "", "vision_available": False})
    source = tmp_path / "source.mp4"
    source.write_bytes(bytes(range(256)) * 10)
    document["asset"]["source"]["uri"] = source.as_uri()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    document["asset"]["source"].update(sha256=digest, size_bytes=source.stat().st_size)
    document["asset"]["id"] = "urn:sha256:" + digest
    folder = tmp_path / "library" / ("a" * 64)
    write_document(folder / "record.fmp.json", document)
    (folder / "frames").mkdir()
    (folder / "frames" / "1.jpg").write_bytes(b"evidence")
    app = create_app(tmp_path / "library", token="testing-token")
    with TestClient(app) as browser:
        yield browser, folder


def test_writes_require_session_token_and_same_origin(client):
    browser, _ = client
    url = "/api/assets/" + "a" * 64 + "/shots/shot-1"
    assert browser.patch(url, json={"revision": 1, "review": {"state": "accepted"}}).status_code == 403
    assert browser.patch(url, json={"revision": 1}, headers={"x-framepulse-token": "testing-token", "origin": "https://evil.example"}).status_code == 403
    assert browser.get("/api/config", headers={"host": "evil.example"}).status_code == 403


def test_human_edit_persists_history_and_stale_revision_fails(client):
    browser, folder = client
    url = "/api/assets/" + "a" * 64 + "/shots/shot-1"
    headers = {"x-framepulse-token": "testing-token"}
    reply = browser.patch(url, json={"revision": 1, "analysis": {"summary": "导演修订"}}, headers=headers)
    assert reply.status_code == 200, reply.text
    doc = read_document(folder / "record.fmp.json")
    assert doc["revision"] == 2
    assert doc["shots"][0]["analysis"]["summary"]["origin"] == "human"
    assert doc["history"][0]["before"]["analysis"]["summary"]["value"] == "一束光"
    assert browser.patch(url, json={"revision": 1, "analysis": {"summary": "stale"}}, headers=headers).status_code == 409
    assert read_document(folder / "record.fmp.json")["revision"] == 2


def test_bad_emotion_and_out_of_bounds_region_do_not_persist(client):
    browser, folder = client
    url = "/api/assets/" + "a" * 64 + "/shots/shot-1"
    reply = browser.patch(url, json={"revision": 1, "emotion": {"tension": 2}}, headers={"x-framepulse-token": "testing-token"})
    assert reply.status_code == 422
    assert read_document(folder / "record.fmp.json")["revision"] == 1


def test_media_range_and_file_allowlist(client):
    browser, _ = client
    prefix = "/api/assets/" + "a" * 64
    reply = browser.get(prefix + "/media", headers={"range": "bytes=100-199"})
    assert reply.status_code == 206
    assert len(reply.content) == 100
    assert browser.get(prefix + "/files/record.fmp.json").status_code == 404
    assert browser.get(prefix + "/files/frames/1.jpg").content == b"evidence"


def test_exports_and_review_confirmation(client):
    browser, _ = client
    prefix = "/api/assets/" + "a" * 64
    reply = browser.patch(prefix + "/shots/shot-1", json={"revision": 1, "review": {"state": "accepted", "note": "已核对"}}, headers={"x-framepulse-token": "testing-token"})
    assert reply.status_code == 200
    assert reply.json()["shots"][0]["analysis"]["summary"]["review_status"] == "accepted"
    assert browser.get(prefix + "/export?format=web-annotation").json()["type"] == "AnnotationPage"
    assert browser.get(prefix + "/export?format=csv").status_code == 200


def test_replaced_source_is_rejected(client):
    browser, folder = client
    source = folder.parent.parent / "source.mp4"
    source.write_bytes(b"different media")
    assert browser.get("/api/assets/" + "a" * 64 + "/media").status_code == 409


def test_asset_lock_blocks_cross_process_editor(client):
    from framepulse.locking import asset_lock
    browser, folder = client
    with asset_lock(folder):
        reply = browser.patch("/api/assets/" + "a" * 64 + "/shots/shot-1", json={"revision": 1}, headers={"x-framepulse-token": "testing-token"})
    assert reply.status_code == 409


def test_folder_queue_is_pending_and_settings_are_secret_free(client, tmp_path):
    browser, folder = client
    headers = {"x-framepulse-token": "testing-token"}
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "image.jpg").write_bytes(b"placeholder")
    (inputs / "ignore.txt").write_text("ignore")
    configured = browser.post("/api/config", json={"provider_url": "http://127.0.0.1:11434/v1", "model": "vision", "profile": "fast", "vision": False, "api_key": "not-in-response-or-disk"}, headers=headers)
    assert configured.status_code == 200
    assert configured.json()["has_api_key"] is True
    assert "not-in-response-or-disk" not in configured.text
    added = browser.post("/api/queue/add", json={"paths": [str(inputs)]}, headers=headers)
    assert added.status_code == 200
    job = added.json()["jobs"][0]
    assert job["state"] == "pending"
    assert job["profile"] == "fast" and job["vision"] is False
    assert browser.get("/api/jobs").json()["queue"]["state"] == "idle"
    assert browser.post("/api/queue/add", json={"paths": [str(inputs)]}, headers=headers).json()["jobs"] == []
    assert browser.post(f"/api/jobs/{job['id']}/cancel", headers=headers).status_code == 200
    assert browser.post(f"/api/jobs/{job['id']}/retry", headers=headers).status_code == 200
    assert "not-in-response-or-disk" not in (folder.parent / "queue.local.json").read_text(encoding="utf-8")
    assert "not-in-response-or-disk" not in (folder.parent / "settings.local.json").read_text()


def test_queue_and_config_mutations_require_token(client):
    browser, _ = client
    for route, body in [("/api/queue/add", {"paths": ["x"]}), ("/api/queue/start", {}), ("/api/config", {"model": "x"})]:
        assert browser.post(route, json=body).status_code == 403
