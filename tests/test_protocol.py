import copy
import json

import pytest

from framepulse.protocol import export_shots_csv, export_web_annotations, read_document, validate_document, write_document


def test_valid_and_bad_evidence_are_distinguished(document):
    assert not validate_document(document)
    document["shots"][0]["analysis"]["summary"]["evidence_ids"] = ["unknown"]
    assert any("unresolved evidence" in e for e in validate_document(document))


def test_frame_on_out_point_is_outside_half_open_range(document):
    document["shots"][0]["keyframes"][0]["time_us"] = 2_000_000
    assert any("outside shot" in e for e in validate_document(document))


def test_overlap_and_missing_tail_fail(document):
    second = copy.deepcopy(document["shots"][0])
    second.update(id="shot-2", index=2)
    second["range"] = {"start_us": 500_000, "end_us": 1_500_000}
    second["keyframes"][0]["id"] = "frame-2"
    second["analysis"]["summary"]["evidence_ids"] = ["frame-2"]
    second["emotion"] = {}
    document["shots"].append(second)
    errors = validate_document(document)
    assert any("overlap" in e for e in errors)
    assert any("complete duration" in e for e in errors)


def test_unknown_emotion_is_not_zero_and_bad_score_rejected(document):
    exported = export_web_annotations(document)
    assert len(exported["items"]) == 1
    document["shots"][0]["emotion"]["valence"]["value"] = 1.5
    assert any("emotion" in e for e in validate_document(document))


def test_region_export_is_single_keyframe_not_rest_of_video(document):
    document["shots"][0]["regions"] = [{"id": "region-1", "keyframe_id": "frame-1", "xywh": [.25, .25, .5, .5], "label": "光", "origin": "human", "confidence": None}]
    assert not validate_document(document)
    item = export_web_annotations(document, evidence_base_uri="https://example.org/bundle/")["items"][-1]
    assert item["target"]["source"] == "https://example.org/bundle/frames/1.jpg"
    assert item["target"]["selector"]["value"] == "xywh=pixel:240,135,480,270"
    assert json.loads(item["body"]["value"])["source_time_us"] == 1_000_000


def test_csv_formula_injection_is_escaped(document):
    document["shots"][0]["analysis"]["summary"]["value"] = '=HYPERLINK("evil")'
    assert "'=HYPERLINK" in export_shots_csv(document)


def test_atomic_write_keeps_existing_document_when_value_invalid(tmp_path, document):
    path = tmp_path / "测试.fmp.json"
    write_document(path, document)
    document["broken"] = float("nan")
    with pytest.raises(ValueError):
        write_document(path, document)
    assert "broken" not in read_document(path)


@pytest.mark.parametrize("uri", ["../private.txt", "C:\\private.txt", "/etc/passwd"])
def test_evidence_must_stay_inside_bundle(document, uri):
    document["shots"][0]["keyframes"][0]["uri"] = uri
    assert any("relative" in e for e in validate_document(document))


def test_nan_is_rejected_at_read_and_validation(tmp_path, document):
    document["shots"][0]["analysis"]["summary"]["confidence"] = float("nan")
    assert validate_document(document)
    path = tmp_path / "bad.json"
    path.write_text('{"value":NaN}')
    with pytest.raises(ValueError):
        read_document(path)


def test_identity_and_image_time_are_checked(document):
    document["asset"]["id"] = "urn:sha256:" + "b" * 64
    assert any("sha256" in e for e in validate_document(document))
    document["asset"]["kind"] = "image"
    document["shots"][0]["range"] = None
    errors = validate_document(document)
    assert any("image duration" in e for e in errors)
    assert any("image time" in e for e in errors)
