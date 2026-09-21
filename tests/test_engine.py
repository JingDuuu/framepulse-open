"""Real decoder integration fixtures: known cuts, VFR PTS, and resumability."""
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import Image
import pytest

from framepulse import engine, media, providers
from framepulse.protocol import claim, read_document, validate_document, write_document


def ffmpeg(*args):
    return media.run_process([media.ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)])


@pytest.fixture
def three_shots(tmp_path):
    source = tmp_path / "中文 空格 三镜.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x90:r=24:d=0.5", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=0.5",
           "-f", "lavfi", "-i", "color=c=green:s=160x90:r=24:d=0.5", "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
           "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
    return source


def test_real_cuts_timestamps_evidence_and_no_invented_emotion(three_shots, tmp_path):
    record = engine.analyze_asset(three_shots, tmp_path / "library")
    doc = read_document(record)
    assert validate_document(doc) == []
    assert doc["asset"]["technical"]["decoded_frames"] == 36
    assert doc["asset"]["technical"]["duration_us"] == 1_500_000
    assert [(shot["range"]["start_us"], shot["range"]["end_us"]) for shot in doc["shots"]] == [(0, 500_000), (500_000, 1_000_000), (1_000_000, 1_500_000)]
    for shot, expected_channel in zip(doc["shots"], [2, 0, 1]):
        assert len(shot["keyframes"]) == 5
        assert len({frame["decode_index"] for frame in shot["keyframes"]}) == 5
        for frame in shot["keyframes"]:
            image = cv2.imdecode(np.fromfile(record.parent / frame["uri"], dtype=np.uint8), cv2.IMREAD_COLOR)
            assert np.argmax(np.mean(image, axis=(0, 1))) == expected_channel
        assert shot["analysis"]["summary"]["value"] is None
        assert all(item["value"] is None for item in shot["emotion"].values())


def test_actual_vfr_pts_and_nonzero_source_offset(tmp_path):
    source = tmp_path / "offset-vfr.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=1", "-vf", "setpts='if(lt(N,12),N/(24*TB),0.5/TB+(N-12)/(12*TB))'",
           "-fps_mode", "vfr", "-output_ts_offset", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
    technical = media.probe_video(source)
    frames, cuts, warnings = media.scan_video(source, technical, 60)
    assert technical["source_first_pts_us"] == 2_000_000
    assert frames[0]["time_us"] == 0
    assert technical["frame_rate_mode"] == "variable"
    assert len(frames) == 24
    assert frames[12]["time_us"] == 500_000
    assert frames[13]["time_us"] == 583_333
    assert technical["time_base"]
    assert technical["timing_frame_exact"] is False


def test_sustained_motion_does_not_create_frame_per_shot(tmp_path):
    source = tmp_path / "moving-bars.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 24, (160, 96))
    base = np.zeros((96, 160, 3), dtype=np.uint8)
    base[:, ::2] = 255
    for index in range(48):
        writer.write(np.roll(base, index % 2, axis=1))
    writer.release()
    technical = media.probe_video(source)
    frames, cuts, _ = media.scan_video(source, technical, 27)
    assert len(frames) == 48
    assert cuts == [0, 48]


def test_exif_display_geometry_and_null_image_time(tmp_path):
    source = tmp_path / "旋转图片.jpg"
    image = Image.new("RGB", (80, 40), "navy")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    doc = read_document(engine.analyze_asset(source, tmp_path / "library"))
    assert validate_document(doc) == []
    assert (doc["asset"]["technical"]["width"], doc["asset"]["technical"]["height"]) == (40, 80)
    assert doc["shots"][0]["range"] is None
    assert doc["shots"][0]["keyframes"][0]["time_us"] is None
    assert doc["shots"][0]["keyframes"][0]["width"] == 40


def test_transparent_hidden_colors_do_not_become_visible_content(tmp_path):
    source = tmp_path / "transparent-red.png"
    Image.new("RGBA", (60, 40), (255, 0, 0, 0)).save(source)
    record = engine.analyze_asset(source, tmp_path / "library")
    doc = read_document(record)
    technical = doc["asset"]["technical"]
    assert technical["has_alpha"] is True
    assert technical["alpha_preview_background"] == "#808080"
    assert technical["visible_pixel_fraction"] == 0
    frame = doc["shots"][0]["keyframes"][0]
    image = np.asarray(Image.open(record.parent / frame["uri"]))
    assert np.max(np.abs(image.astype(float) - 128)) <= 1
    assert "display_notes" in doc["shots"][0]["extensions"]
    assert doc["shots"][0]["analysis"]["summary"]["value"] is None


def result_for(shot):
    return providers.normalize_result({"analysis": {"summary": {"value": "受控测试描述", "confidence": 0.9}}, "emotion": {"tension": {"value": 0.2}}}, [frame["id"] for frame in shot["keyframes"]])


def test_cancel_checkpoint_resume_and_keep_human_review(three_shots, tmp_path, monkeypatch):
    cancel = threading.Event()
    calls = []
    def fake(shot, *args, **kwargs):
        calls.append(shot["index"])
        if len(calls) == 2:
            cancel.set()
            raise media.AnalysisCancelled("test cancellation")
        return result_for(shot)
    monkeypatch.setattr(providers, "analyze_shot", fake)
    library = tmp_path / "library"
    with pytest.raises(media.AnalysisCancelled):
        engine.analyze_asset(three_shots, library, vision=True, provider_url="http://localhost:9000/v1", model="fixture", cancel=cancel.is_set)
    record = next(library.glob("*/record.fmp.json"))
    doc = read_document(record)
    assert doc["shots"][0]["status"] == "analyzed"
    assert doc["shots"][1]["status"] == "measured"
    assert doc["runs"][-1]["status"] == "cancelled"
    human = claim("导演确认的构图", "human", evidence_ids=[doc["shots"][1]["keyframes"][0]["id"]])
    human["review_status"] = "edited"
    doc["shots"][1]["analysis"]["composition"] = human
    doc["shots"][2]["review"] = {"state": "accepted", "note": "keep accepted whole shot"}
    doc["shots"][2]["analysis"]["summary"] = claim("已确认描述", "human")
    write_document(record, doc)
    cancel.clear()
    calls.clear()
    monkeypatch.setattr(providers, "analyze_shot", lambda shot, *args, **kwargs: calls.append(shot["index"]) or result_for(shot))
    engine.analyze_asset(three_shots, library, vision=True, provider_url="http://localhost:9000/v1", model="fixture")
    recovered = read_document(record)
    assert calls == [2]
    assert recovered["shots"][1]["analysis"]["composition"] == human
    assert recovered["shots"][2]["analysis"]["summary"]["value"] == "已确认描述"
    assert recovered["shots"][2]["review"]["state"] == "accepted"
    assert validate_document(recovered) == []


def test_failed_shot_retries_without_repeating_success(three_shots, tmp_path, monkeypatch):
    calls = []
    def first(shot, *args, **kwargs):
        if shot["index"] == 2:
            raise providers.ProviderError("controlled unavailable model")
        return result_for(shot)
    monkeypatch.setattr(providers, "analyze_shot", first)
    library = tmp_path / "library"
    record = engine.analyze_asset(three_shots, library, vision=True, provider_url="http://localhost:9000/v1", model="fixture")
    doc = read_document(record)
    assert [shot["status"] for shot in doc["shots"]] == ["analyzed", "failed", "analyzed"]
    monkeypatch.setattr(providers, "analyze_shot", lambda shot, *args, **kwargs: calls.append(shot["index"]) or result_for(shot))
    engine.analyze_asset(three_shots, library, vision=True, provider_url="http://localhost:9000/v1", model="fixture")
    assert calls == [2]
    assert all(shot["status"] == "analyzed" for shot in read_document(record)["shots"])


def test_cancel_before_decode_leaves_no_false_completed_record(tmp_path):
    source = tmp_path / "image.png"
    Image.new("RGB", (40, 30)).save(source)
    with pytest.raises(media.AnalysisCancelled):
        engine.analyze_asset(source, tmp_path / "library", cancel=lambda: True)
    assert not list((tmp_path / "library").glob("*/record.fmp.json"))


def test_changed_measurement_configuration_rebuilds_and_archives_review(three_shots, tmp_path):
    library = tmp_path / "library"
    record = engine.analyze_asset(three_shots, library, threshold=27)
    doc = read_document(record)
    doc["shots"][0]["review"] = {"state": "accepted", "note": "original boundaries reviewed"}
    original_shot_id = doc["shots"][0]["id"]
    write_document(record, doc)
    # 250 intentionally disables these known RGB cuts. A changed threshold must
    # not silently reuse the previous three-shot segmentation.
    engine.analyze_asset(three_shots, library, threshold=250, resume=True)
    changed = read_document(record)
    assert len(changed["shots"]) == 1
    assert any(shot["id"] == original_shot_id for shot in changed["extensions"]["archived_reviewed_shots"])
    assert changed["history"][-1]["action"] == "remeasurement"
    assert validate_document(changed) == []


def test_changed_model_reanalyzes_and_archives_old_ai(tmp_path, monkeypatch):
    source = tmp_path / "image.png"
    Image.new("RGB", (40, 30), "blue").save(source)
    calls = []
    def fake(shot, bundle, url, model, *args, **kwargs):
        calls.append(model)
        value = result_for(shot)
        value["analysis"]["summary"]["value"] = "description by " + model
        return value
    monkeypatch.setattr(providers, "analyze_shot", fake)
    library = tmp_path / "library"
    kwargs = {"vision": True, "provider_url": "http://localhost:9000/v1"}
    record = engine.analyze_asset(source, library, model="first", **kwargs)
    engine.analyze_asset(source, library, model="first", **kwargs)
    assert calls == ["first"]
    engine.analyze_asset(source, library, model="second", **kwargs)
    assert calls == ["first", "second"]
    changed = read_document(record)
    assert changed["shots"][0]["analysis"]["summary"]["value"] == "description by second"
    archived = next(item for item in changed["history"] if item["action"] == "vision-reanalysis")
    assert archived["before"]["analysis"]["summary"]["value"] == "description by first"
    assert validate_document(changed) == []


@pytest.mark.parametrize("profile,count", [("fast", 3), ("balanced", 5), ("detailed", 7)])
def test_profiles_keep_full_pts_scan_with_different_evidence_density(three_shots, tmp_path, profile, count):
    doc = read_document(engine.analyze_asset(three_shots, tmp_path / profile, profile=profile))
    assert doc["asset"]["technical"]["decoded_frames"] == 36
    assert doc["asset"]["technical"]["duration_us"] == 1_500_000
    assert all(len(shot["keyframes"]) == count for shot in doc["shots"])
    assert doc["extensions"]["profile"] == profile
    assert doc["overview"]["status"] == "measured"
    assert doc["overview"]["analysis"]["summary"]["value"] is None


def test_overlong_metadata_is_rejected_before_reading_entire_file(three_shots, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "probe_video", lambda *args: {"duration_us": media.MAX_VIDEO_DURATION_US + 1, "duration_is_stream_relative": True})
    def forbidden(*args):
        raise AssertionError("hash should not run for known overlong video")
    monkeypatch.setattr(media, "sha256_file", forbidden)
    with pytest.raises(ValueError, match="45"):
        engine.analyze_asset(three_shots, tmp_path / "library")


def test_duration_cap_handles_source_pts_offset_without_false_rejection():
    media.enforce_duration_limit({"duration_us": media.MAX_VIDEO_DURATION_US + 2_000_000, "reported_start_time_us": 2_000_000})
    media.enforce_duration_limit({"duration_us": media.MAX_VIDEO_DURATION_US}, decoded=True)
    with pytest.raises(ValueError, match="45"):
        media.enforce_duration_limit({"duration_us": media.MAX_VIDEO_DURATION_US + 1}, decoded=True)


def test_scan_enforces_duration_even_when_container_metadata_lies(three_shots, monkeypatch):
    technical = media.probe_video(three_shots)
    technical["duration_us"] = 100_000
    monkeypatch.setattr(media, "MAX_VIDEO_DURATION_US", 500_000)
    with pytest.raises(ValueError, match="45"):
        media.scan_video(three_shots, technical, 27)


def test_measurement_plan_survives_cancelled_extraction(three_shots, tmp_path, monkeypatch):
    library = tmp_path / "library"
    original_extract = media.extract_keyframes
    monkeypatch.setattr(media, "extract_keyframes", lambda *args, **kwargs: (_ for _ in ()).throw(media.AnalysisCancelled("cancel extraction")))
    with pytest.raises(media.AnalysisCancelled):
        engine.analyze_asset(three_shots, library)
    assert next(library.glob("*/.measurement-cache.json")).is_file()
    monkeypatch.setattr(media, "extract_keyframes", original_extract)
    monkeypatch.setattr(media, "scan_video", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must resume scan plan, not decode again")))
    record = engine.analyze_asset(three_shots, library)
    assert len(read_document(record)["shots"]) == 3
    assert not list(library.glob("*/.measurement-cache.json"))


def test_midrun_reader_sees_journal_and_controlled_cancel_compacts(three_shots, tmp_path, monkeypatch):
    library = tmp_path / "library"
    observed = []
    def fake(shot, *args, **kwargs):
        if shot["index"] == 2:
            record = next(library.glob("*/record.fmp.json"))
            observed.append(read_document(record)["shots"][0]["status"])
            assert record.with_name("record.fmp.journal.jsonl").is_file()
            raise media.AnalysisCancelled("stop after durable first shot")
        return result_for(shot)
    monkeypatch.setattr(providers, "analyze_shot", fake)
    with pytest.raises(media.AnalysisCancelled):
        engine.analyze_asset(three_shots, library, vision=True, provider_url="http://localhost:9000/v1", model="fixture")
    record = next(library.glob("*/record.fmp.json"))
    assert observed == ["analyzed"]
    assert not record.with_name("record.fmp.journal.jsonl").exists()
    assert read_document(record)["runs"][-1]["status"] == "cancelled"


def test_balanced_frame_selector_extracts_actual_requested_frames(three_shots, tmp_path):
    technical = media.probe_video(three_shots)
    indices = [0, 1, 5, 11, 12, 19, 23, 24, 33, 35]
    result = media.extract_keyframes(three_shots, tmp_path, indices, technical)
    assert sorted(result) == indices
    for index, frame in result.items():
        pixels = cv2.imdecode(np.fromfile(tmp_path / frame["uri"], dtype=np.uint8), cv2.IMREAD_COLOR)
        assert np.argmax(np.mean(pixels, axis=(0, 1))) == [2, 0, 1][index // 12]
    # A large arbitrary selection remains logarithmic in branch depth.
    expr = media._selection_expression(range(0, 100_000, 17))
    depth = maximum = 0
    for character in expr:
        depth += character == "("
        depth -= character == ")"
        maximum = max(maximum, depth)
    assert maximum < 20
