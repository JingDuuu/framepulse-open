"""FMP 0.1 sidecars, structural + semantic validation, and interchange adapters.

The format is experimental. Time values are integer microseconds, [in, out).
Missing and inferred values remain distinguishable from measured information.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
import uuid
from urllib.parse import urljoin
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = "framepulse.media"
SPEC_VERSION = "0.1.0"
NAMESPACE = uuid.UUID("a5fa082a-c33a-40b0-9610-4179ca3c408f")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stable_id(*parts: Any) -> str:
    return "urn:uuid:" + str(uuid.uuid5(NAMESPACE, json.dumps(parts, ensure_ascii=False, separators=(",", ":"))))


def claim(value: Any, origin: str = "measured", confidence: float | None = None,
          evidence_ids: list[str] | None = None, run_id: str | None = None) -> dict:
    return dict(value=value, origin=origin, confidence=confidence,
                evidence_ids=evidence_ids or [], run_id=run_id,
                review_status="unreviewed")


def read_document(path: str | Path) -> dict:
    def invalid_constant(value):
        raise ValueError(f"JSON 不允许非有限数值: {value}")
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle, parse_constant=invalid_constant)


def write_document(path: str | Path, doc: dict) -> None:
    """Atomic replace on the same volume; reject NaN and never expose partial JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=".fmp-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_document(doc: Any) -> list[str]:
    import jsonschema
    try:
        json.dumps(doc, allow_nan=False)
    except (ValueError, TypeError):
        return ["document contains a non-JSON value or non-finite number"]
    schema = read_document(Path(__file__).parent / "schemas" / "fmp-0.1.schema.json")
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).iter_errors(doc)]
    if errors:
        return errors[:50]
    ids: set[str] = set()
    run_ids = {r["id"] for r in doc["runs"]}
    kind = doc["asset"]["kind"]
    duration = doc["asset"]["technical"].get("duration_us")
    if doc["asset"]["id"] != "urn:sha256:" + doc["asset"]["source"]["sha256"]:
        errors.append("asset.id does not match source.sha256")
    if kind == "image" and duration is not None:
        errors.append("image duration_us must be null")
    last_end = 0
    for shot in doc["shots"]:
        for entity in [shot, *shot["keyframes"], *shot.get("regions", [])]:
            if entity["id"] in ids:
                errors.append(f"duplicate entity id: {entity['id']}")
            ids.add(entity["id"])
        span = shot["range"]
        if kind == "video":
            if not span or span["end_us"] <= span["start_us"]:
                errors.append(f"{shot['id']}: video range must have positive duration")
            elif span["start_us"] != last_end:
                errors.append(f"{shot['id']}: shot ranges must partition the source without gaps/overlap")
            if span:
                last_end = span["end_us"]
        elif span is not None:
            errors.append(f"{shot['id']}: image range must be null")
        frame_ids = {f["id"] for f in shot["keyframes"]}
        for frame in shot["keyframes"]:
            uri = frame["uri"].replace("\\", "/")
            if not uri.strip() or uri.startswith("/") or ":" in uri or ".." in uri.split("/"):
                errors.append(f"{frame['id']}: keyframe URI must be a bundle-relative path")
            if span and (frame["time_us"] is None or not span["start_us"] <= frame["time_us"] < span["end_us"]):
                errors.append(f"{frame['id']}: evidence frame lies outside shot")
            if kind == "image" and frame["time_us"] is not None:
                errors.append(f"{frame['id']}: image time_us must be null")
        for group in ("analysis", "emotion"):
            for field, item in shot.get(group, {}).items():
                if any(e not in frame_ids for e in item["evidence_ids"]):
                    errors.append(f"{shot['id']}/{field}: unresolved evidence")
                if item["run_id"] and item["run_id"] not in run_ids:
                    errors.append(f"{shot['id']}/{field}: unresolved run")
                if group == "emotion" and item["value"] is not None:
                    val = item["value"]
                    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 <= val <= 1:
                        errors.append(f"{shot['id']}/{field}: emotion must be null or in [0,1]")
        for region in shot.get("regions", []):
            x, y, w, h = region["xywh"]
            if w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
                errors.append(f"{region['id']}: rectangle extends outside image or is empty")
            if region["keyframe_id"] not in frame_ids:
                errors.append(f"{region['id']}: unresolved region keyframe")
    if kind == "video" and last_end != duration:
        errors.append("shot ranges must cover the complete duration")
    if kind == "image" and len(doc["shots"]) != 1:
        errors.append("image assets must have exactly one image unit")
    if len(run_ids) != len(doc["runs"]):
        errors.append("duplicate run id")
    return errors


def export_shots_csv(doc: dict) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    fields = ["summary", "shot_size", "camera_angle", "camera_movement", "composition", "actions", "lighting", "narrative_function", "tags"]
    writer.writerow(["asset_id", "shot_id", "index", "start_us", "end_us", "review", *fields,
                     "valence", "arousal", "tension", "evidence", "note"])

    def safe(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value

    for shot in doc["shots"]:
        span = shot["range"] or {}
        row = [doc["asset"]["id"], shot["id"], shot["index"], span.get("start_us"), span.get("end_us"), shot["review"]["state"]]
        row += [shot["analysis"].get(f, {}).get("value") for f in fields]
        row += [shot["emotion"].get(f, {}).get("value") for f in ("valence", "arousal", "tension")]
        row += [[k["uri"] for k in shot["keyframes"]], shot["review"].get("note", "")]
        writer.writerow([safe(v) for v in row])
    return "\ufeff" + out.getvalue()


def export_web_annotations(doc: dict, source_uri: str | None = None, evidence_base_uri: str | None = None) -> dict:
    """W3C AnnotationPage; preserve claim provenance inside a JSON textual body.

    file: URI is usable locally. Consumers should supply a public URI when sharing.
    This adapter does not claim full semantic alignment with all W3C vocabularies.
    """
    source = source_uri or doc["asset"]["source"]["uri"]
    items = []
    for shot in doc["shots"]:
        target: dict = {"type": "SpecificResource", "source": source}
        if shot["range"]:
            r = shot["range"]
            target["selector"] = {"type": "FragmentSelector", "conformsTo": "http://www.w3.org/TR/media-frags/",
                                  "value": f"t=npt:{r['start_us']/1e6:.6f},{r['end_us']/1e6:.6f}"}
        for group in ("analysis", "emotion"):
            for field, item in shot[group].items():
                if item["value"] is None:
                    continue
                items.append({"id": stable_id(doc["id"], shot["id"], group, field), "type": "Annotation",
                              "motivation": "describing", "created": doc["updated_at"], "target": target,
                              "body": {"type": "TextualBody", "format": "application/json", "purpose": "describing",
                                       "value": json.dumps({"field": f"{group}.{field}", **item}, ensure_ascii=False)}})
        for region in shot.get("regions", []):
            frame = next(f for f in shot["keyframes"] if f["id"] == region["keyframe_id"])
            width = frame["width"]
            height = frame["height"]
            x, y, w, h = region["xywh"]
            left, top = int(x * width), int(y * height)
            right, bottom = min(width, math.ceil((x+w)*width)), min(height, math.ceil((y+h)*height))
            selector = f"xywh=pixel:{left},{top},{right-left},{bottom-top}"
            items.append({"id": stable_id(doc["id"], region["id"]), "type": "Annotation", "motivation": "tagging",
                          "target": {"type": "SpecificResource", "source": urljoin(evidence_base_uri or "", frame["uri"]),
                                     "selector": {"type": "FragmentSelector", "conformsTo": "http://www.w3.org/TR/media-frags/", "value": selector}},
                          "body": {"type": "TextualBody", "format": "application/json", "purpose": "tagging",
                                   "value": json.dumps({**region, "source_media": source, "source_time_us": frame["time_us"]}, ensure_ascii=False)}})
    return {"@context": "http://www.w3.org/ns/anno.jsonld", "id": stable_id(doc["id"], "annotations"),
            "type": "AnnotationPage", "items": items}
