import pytest

from framepulse.protocol import claim, now_iso


@pytest.fixture
def document():
    now = now_iso()
    return {"format": "framepulse.media", "spec_version": "0.1.0", "id": "urn:uuid:document", "revision": 1,
            "created_at": now, "updated_at": now,
            "asset": {"id": "urn:sha256:" + "a" * 64, "kind": "video", "name": "镜头 空格.mp4",
                      "source": {"uri": "file:///C:/fixtures/video.mp4", "sha256": "a" * 64, "size_bytes": 123},
                      "technical": {"width": 1920, "height": 1080, "duration_us": 2_000_000,
                                    "frame_rate": {"numerator": 30000, "denominator": 1001}}},
            "time": {"unit": "microseconds", "origin": "first_presented_frame", "interval": "half-open"},
            "shots": [{"id": "shot-1", "index": 1, "range": {"start_us": 0, "end_us": 2_000_000},
                       "boundary": {"method": "test", "confidence": None},
                       "keyframes": [{"id": "frame-1", "time_us": 1_000_000, "uri": "frames/1.jpg", "width": 960, "height": 540}],
                       "analysis": {"summary": claim("一束光", "inferred", .8, ["frame-1"], "run-1")},
                       "emotion": {"valence": claim(None, "inferred", None, ["frame-1"], "run-1")},
                       "regions": [], "review": {"state": "unreviewed", "note": ""}, "status": "analyzed", "warnings": []}],
            "runs": [{"id": "run-1", "kind": "vision", "provider": "fixture", "model": "fixture", "started_at": now, "status": "completed"}],
            "history": [], "warnings": [], "extensions": {}}
