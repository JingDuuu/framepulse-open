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
from collections import Counter
from copy import deepcopy
from functools import lru_cache
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
    path = Path(path)
    with open(path, "r", encoding="utf-8-sig") as handle:
        doc = json.load(handle, parse_constant=invalid_constant)
    journal = _journal_path(path)
    if path.name != "record.fmp.json" or not journal.is_file():
        return doc
    changed = False
    with journal.open("rb") as handle:
        for line in handle:
            # A crashed append can leave an incomplete final line. Earlier
            # durable lines remain authoritative; never guess a partial event.
            if not line.endswith(b"\n"):
                break
            event = json.loads(line, parse_constant=invalid_constant)
            if event.get("format") != "framepulse.checkpoint" or event.get("document_id") != doc.get("id"):
                raise ValueError("检查点日志与素材记录不匹配")
            revision = event.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool):
                raise ValueError("检查点 revision 无效")
            if revision <= doc["revision"]:
                continue  # Compacted or superseded by a later human edit.
            if revision != doc["revision"] + 1:
                raise ValueError("检查点日志缺少连续修订，不能安全恢复")
            shot = event["shot"]
            position = next((i for i, current in enumerate(doc["shots"]) if current["id"] == shot["id"]), None)
            if position is None:
                raise ValueError("检查点引用了不存在的镜头")
            doc["shots"][position] = shot
            run = event.get("run")
            if run:
                position = next((i for i, current in enumerate(doc["runs"]) if current["id"] == run["id"]), None)
                if position is None:
                    doc["runs"].append(run)
                else:
                    doc["runs"][position] = run
            doc["history"].extend(event.get("history_append", []))
            doc.update(revision=revision, updated_at=event["updated_at"])
            changed = True
    if changed:
        doc["overview"] = build_overview(doc)
    return doc


def _journal_path(path: Path) -> Path:
    return path.with_name("record.fmp.journal.jsonl")


def append_shot_checkpoint(path: str | Path, doc: dict, shot: dict, history_append: list | None = None) -> None:
    """Private live journal. Caller owns the asset lock; final JSON is standalone."""
    path = Path(path)
    errors = validate_shot(shot, {run["id"] for run in doc["runs"]})
    if errors:
        raise ValueError("镜头检查点校验失败: " + "; ".join(errors[:4]))
    revision = doc["revision"] + 1
    updated_at = now_iso()
    event = {"format": "framepulse.checkpoint", "document_id": doc["id"], "revision": revision,
             "updated_at": updated_at, "shot": shot, "run": doc["runs"][-1] if doc["runs"] else None,
             "history_append": history_append or []}
    encoded = (json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    journal = _journal_path(path)
    with journal.open("a+b") as handle:
        handle.seek(0)
        existing = handle.read()
        if existing and not existing.endswith(b"\n"):
            handle.seek(existing.rfind(b"\n") + 1)
            handle.truncate()
        handle.seek(0, os.SEEK_END)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    doc.update(revision=revision, updated_at=updated_at)


def compact_document(path: str | Path, doc: dict) -> None:
    """Publish a complete snapshot before removing superseded private events."""
    write_document(path, doc)
    _journal_path(Path(path)).unlink(missing_ok=True)


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


@lru_cache(maxsize=1)
def _schema():
    return read_document(Path(__file__).parent / "schemas" / "fmp-0.1.schema.json")


def validate_shot(shot: dict, run_ids: set[str]) -> list[str]:
    import jsonschema
    try:
        json.dumps(shot, allow_nan=False)
    except (ValueError, TypeError):
        return ["shot contains a non-JSON value or non-finite number"]
    schema = _schema()
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/shot", "$defs": schema["$defs"]})
    errors = [error.message for error in validator.iter_errors(shot)]
    if errors:
        return errors[:20]
    frame_ids = {frame["id"] for frame in shot["keyframes"]}
    entity_ids = [shot["id"], *[frame["id"] for frame in shot["keyframes"]], *[region["id"] for region in shot["regions"]]]
    if len(set(entity_ids)) != len(entity_ids):
        errors.append("duplicate entity id")
    for group in ("analysis", "emotion"):
        for field, item in shot[group].items():
            if any(identifier not in frame_ids for identifier in item["evidence_ids"]):
                errors.append(f"{field}: unresolved evidence")
            if item["run_id"] is not None and item["run_id"] not in run_ids:
                errors.append(f"{field}: unresolved run")
            if group == "emotion" and item["value"] is not None and (isinstance(item["value"], bool) or not isinstance(item["value"], (float, int)) or not 0 <= item["value"] <= 1):
                errors.append(f"{field}: emotion must be null or in [0,1]")
    for region in shot["regions"]:
        x, y, width, height = region["xywh"]
        if width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
            errors.append("invalid region geometry")
        if region["keyframe_id"] not in frame_ids:
            errors.append("unresolved region keyframe")
    return errors


def validate_document(doc: Any) -> list[str]:
    import jsonschema
    try:
        json.dumps(doc, allow_nan=False)
    except (ValueError, TypeError):
        return ["document contains a non-JSON value or non-finite number"]
    schema = _schema()
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
    overview = doc.get("overview")
    if overview:
        shot_ids = {shot["id"] for shot in doc["shots"]}
        frame_ids = {frame["id"] for shot in doc["shots"] for frame in shot["keyframes"]}
        if any(identifier not in shot_ids for identifier in overview["source_shot_ids"]):
            errors.append("overview: unresolved shot")
        if overview["coverage"]["total_shots"] != len(doc["shots"]):
            errors.append("overview: coverage total does not match shots")
        expected_coverage = build_overview(doc)["coverage"]
        for field, count in expected_coverage.items():
            if overview["coverage"][field] != count:
                errors.append(f"overview: {field} does not match current shot evidence")
        for name, item in overview["analysis"].items():
            if any(identifier not in frame_ids for identifier in item["evidence_ids"]):
                errors.append(f"overview/{name}: unresolved evidence")
            if item["run_id"] is not None and item["run_id"] not in run_ids:
                errors.append(f"overview/{name}: unresolved run")
            if any(identifier not in shot_ids for identifier in item.get("source_shot_ids", [])):
                errors.append(f"overview/{name}: unresolved shot")
    return errors


VISUAL_MEDIA = {"2d_animation", "3d_animation", "live_action", "mixed", "screen_capture", "graphic", "unknown"}


def _has_semantic_claims(shot):
    if shot["review"]["state"] == "rejected":
        return False
    for field in ("summary", "subjects", "actions", "tags", "visual_medium", "themes"):
        item = shot["analysis"].get(field, {})
        value = item.get("value")
        if value and value != "unknown" and item.get("review_status") != "rejected":
            return True
    return False


def build_overview(doc: dict) -> dict:
    """Extractive, source-backed index; never infer an unseen whole-film plot."""
    shots = doc["shots"]
    analyzed = [shot for shot in shots if shot["status"] == "analyzed"]
    semantic = [shot for shot in shots if _has_semantic_claims(shot)]
    coverage = {"total_shots": len(shots), "analyzed_shots": len(analyzed), "semantic_shots": len(semantic),
                "abstained_shots": sum(shot.get("extensions", {}).get("vision_status") == "abstained" for shot in shots),
                "failed_shots": sum(shot["status"] == "failed" for shot in shots)}
    status = "completed" if len(analyzed) == len(shots) else "partial" if analyzed or semantic else "measured"
    result = {"status": status, "coverage": coverage, "analysis": {}, "source_shot_ids": [shot["id"] for shot in semantic],
              "warnings": ["概括按已标注镜头做摘录汇总；叙事用途仍是原镜头推断，不代表经确认的整片剧情。"]}
    if len(semantic) < len(shots):
        result["warnings"].append(f"仅 {len(semantic)}/{len(shots)} 个镜头具有可用视觉语义，其余内容未知。")
    def collect(field):
        entries = []
        for shot in semantic:
            item = shot["analysis"].get(field, {})
            value = item.get("value")
            if value is not None and value != [] and value != "" and item.get("review_status") != "rejected":
                entries.append((shot, item, value))
        return entries
    def make(field, value, entries):
        ids = list(dict.fromkeys(identifier for _, item, _ in entries for identifier in item.get("evidence_ids", [])))
        source_ids = list(dict.fromkeys(shot["id"] for shot, _, _ in entries))
        output = claim(value, "inferred", None, ids, None)
        output.update(source_shot_ids=source_ids, aggregation="extractive-source-claims")
        result["analysis"][field] = output
    summaries = collect("summary")
    distinct, used = [], []
    for shot, item, value in summaries:
        if isinstance(value, str) and value not in distinct:
            distinct.append(value)
            used.append((shot, item, value))
    # Evenly sample a bounded number of observed summaries across a long film.
    if len(used) > 8:
        used = [used[round(i * (len(used) - 1) / 7)] for i in range(8)]
    summary = "；".join(f"镜头 {shot['index']}：{str(value)[:140]}" for shot, _, value in used) or None
    if summary:
        summary = f"已标注 {len(semantic)}/{len(shots)} 镜。" + summary
    make("summary", summary, used)
    for output_field, source_field in (("visual_medium", "visual_medium"), ("themes", "themes"), ("tags", "tags"), ("subjects", "subjects")):
        entries = collect(source_field)
        values, retained = [], []
        for entry in entries:
            value = entry[2]
            options = value if isinstance(value, list) else [value]
            valid = [item.strip()[:160] for item in options if isinstance(item, str) and item.strip() and (output_field != "visual_medium" or item in VISUAL_MEDIA - {"unknown"})]
            if valid:
                values.extend(valid)
                retained.append(entry)
        counts = Counter(values)
        ranked = [name for name, _ in counts.most_common(40)]
        make(output_field, ranked or None, retained)
    narrative = collect("narrative_function")
    narrative = [entry for entry in narrative if isinstance(entry[2], str)][:6]
    value = "；".join(f"镜头 {shot['index']} 的用途推断：{text[:160]}" for shot, _, text in narrative) or None
    make("narrative", value, narrative)
    for field, item in doc.get("overview", {}).get("analysis", {}).items():
        if item.get("origin") == "human" or item.get("review_status") in {"accepted", "edited"}:
            result["analysis"][field] = deepcopy(item)
    return result


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
