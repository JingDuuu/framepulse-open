"""FramePulse's reusable single-asset analysis pipeline.

Measurement never needs a model or uploads media. Vision and OCR are explicit
stages with independent provenance and per-shot atomic checkpoints.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import importlib.util
import hashlib
import json
import math
import os
import uuid

import cv2
import numpy as np

from . import media, providers
from .locking import asset_lock
from .media import AnalysisCancelled, check_cancel, emit
from .protocol import claim, now_iso, read_document, stable_id, validate_document, write_document

DETECTOR_VERSION = "adaptive-hsv-histogram-v1"


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _measurement_fingerprint(threshold):
    return _fingerprint({"threshold": float(threshold), "detector": DETECTOR_VERSION, "evidence_selection": "five-temporal-quality-v1"})


def _vision_fingerprint(shot, provider_url, model):
    parsed, path, native = providers.provider_endpoint(provider_url)
    data = {"endpoint": parsed.scheme + "://" + parsed.netloc + path, "model": model,
                         "prompt": providers.SYSTEM_PROMPT, "response_schema": providers._output_schema([f"K{i+1}" for i in range(min(5, len(shot["keyframes"])))]),
                         "evidence": shot["keyframes"][:5], "adapter": "native-compact-schema-8k-v3" if native else "compatible-compact-v3"}
    if shot.get("extensions", {}).get("display_notes"):
        data["display_notes"] = shot["extensions"]["display_notes"]
    return _fingerprint(data)


def capabilities():
    try:
        ffmpeg = media.ffmpeg_executable()
        ffprobe = media.ffprobe_executable()
    except (ImportError, RuntimeError, ValueError):
        ffmpeg = ffprobe = None
    return {"ffmpeg": {"available": bool(ffmpeg), "path": ffmpeg},
            "ffprobe": {"available": bool(ffprobe), "path": ffprobe},
            "image": True, "video": bool(ffmpeg), "shot_detection": "adaptive-full-frame-hsv-histogram",
            "vision": {"available": True, "requires_configuration": True, "providers": ["openai-compatible", "ollama"],
                       "automatic_downloads": False, "default_upload": False},
            "ocr": {"available": importlib.util.find_spec("rapidocr_onnxruntime") is not None, "engine": "RapidOCR (local)"},
            "audio_transcription": False, "emotion": "optional-vision-inference; never derived from luminance",
            "time_precision": "decoded PTS rounded to integer microseconds; not certified frame-exact"}


def _protected(item):
    return item.get("origin") == "human" or item.get("review_status") in {"accepted", "edited"}


def _preserve_reviewed(doc, previous):
    old_shots = {shot["id"]: shot for shot in previous.get("shots", [])}
    for shot in doc["shots"]:
        old = old_shots.get(shot["id"])
        if not old:
            continue
        protect_whole = old.get("review", {}).get("state") in {"accepted", "edited", "rejected"}
        if protect_whole:
            shot["review"] = deepcopy(old["review"])
            shot["status"] = old["status"]
        for group in ("analysis", "emotion"):
            for field, value in old.get(group, {}).items():
                if protect_whole or _protected(value):
                    shot.setdefault(group, {})[field] = deepcopy(value)
        if protect_whole:
            shot["regions"] = deepcopy(old.get("regions", []))
        else:
            human_regions = [deepcopy(region) for region in old.get("regions", []) if region.get("origin") == "human"]
            human_ids = {region["id"] for region in human_regions}
            shot["regions"] = human_regions + [region for region in shot.get("regions", []) if region["id"] not in human_ids and region.get("origin") != "human"]
        needed_ids = {identifier for group in ("analysis", "emotion") for item in shot.get(group, {}).values() for identifier in item.get("evidence_ids", [])}
        needed_ids.update(region["keyframe_id"] for region in shot.get("regions", []))
        current_ids = {frame["id"] for frame in shot["keyframes"]}
        shot["keyframes"] += [deepcopy(frame) for frame in old["keyframes"] if frame["id"] in needed_ids and frame["id"] not in current_ids]
    # Provenance of preserved claims must still resolve after a remeasurement.
    run_ids = {run["id"] for run in doc["runs"]}
    doc["runs"] += [deepcopy(run) for run in previous.get("runs", []) if run["id"] not in run_ids]
    if len(previous.get("history", [])) > len(doc.get("history", [])):
        doc["history"] = deepcopy(previous["history"])


def _checkpoint(path, doc):
    if path.is_file():
        previous = read_document(path)
        _preserve_reviewed(doc, previous)
        doc["revision"] = max(doc.get("revision", 0), previous.get("revision", 0)) + 1
    doc["updated_at"] = now_iso()
    errors = validate_document(doc)
    if errors:
        raise ValueError("内部记录校验失败: " + "; ".join(errors[:5]))
    write_document(path, doc)


def _run(kind, provider, model=None, config=None):
    return {"id": "urn:uuid:" + str(uuid.uuid4()), "kind": kind, "provider": provider, "model": model,
            "started_at": now_iso(), "completed_at": None, "status": "running", "config": config or {}}


def _shot(sha, index, span, keyframes, run, score=None):
    identity = stable_id(sha, "shot", span)
    return {"id": identity, "index": index, "range": span,
            "boundary": {"method": "adaptive-hsv-histogram" if span else "image-unit", "confidence": None, "score": score},
            "keyframes": keyframes, "analysis": {field: claim(None, "inferred") for field in providers.ANALYSIS_FIELDS},
            "emotion": {field: claim(None, "inferred") for field in providers.EMOTION_FIELDS}, "regions": [],
            "review": {"state": "unreviewed", "note": ""}, "status": "measured", "warnings": []}


def _measure(source, bundle, sha, threshold, cancel, progress):
    run = _run("measurement", "framepulse-ffmpeg", config={"threshold": threshold, "detector": DETECTOR_VERSION, "frame_sampling": "all-decoded-frames", "keyframes": "five-temporal-bins-quality-selection"})
    doc = {"format": "framepulse.media", "spec_version": "0.1.0", "id": stable_id("framepulse.media", sha), "revision": 1,
           "created_at": now_iso(), "updated_at": now_iso(),
           "asset": {"id": "urn:sha256:" + sha, "kind": "image" if source.suffix.lower() in media.IMAGE_EXTENSIONS else "video",
                     "name": source.name, "source": {"uri": source.as_uri(), "sha256": sha, "size_bytes": source.stat().st_size}, "technical": {}},
           "time": {"unit": "microseconds", "origin": "first_presented_frame", "interval": "half-open"},
           "shots": [], "runs": [run], "history": [], "warnings": [], "extensions": {"generator": "FramePulse Open 0.1.0", "measurement_fingerprint": _measurement_fingerprint(threshold)}}
    if doc["asset"]["kind"] == "image":
        emit(progress, "measure", 0, 1, "读取图片尺寸与方向")
        technical, metrics, evidence, warnings = media.prepare_image(source, bundle, cancel)
        run["provider"] = "framepulse-pillow"
        keyframe = {"id": stable_id(sha, "frame", "image"), "time_us": None, **evidence}
        shot = _shot(sha, 1, None, [keyframe], run)
        if technical.get("has_alpha"):
            notes = ["图片透明部分使用 #808080 灰底预览。该背景为查看工具添加，不是素材内容，不应识别为场景或物体。"]
            if technical.get("visible_pixel_fraction") == 0:
                notes.append("源图片全部透明，没有任何有效可见像素；隐藏RGB不是内容，所有视觉语义/情绪都应为null，regions为空。")
            shot["extensions"] = {"display_notes": notes}
        for field in ("brightness", "saturation", "sharpness", "dominant_colors"):
            shot["analysis"][field] = claim(metrics[field], "measured", None, [keyframe["id"]], run["id"])
        doc["shots"].append(shot)
        doc["warnings"].extend(warnings)
    else:
        emit(progress, "probe", 0, 1, "读取视频与音轨元数据")
        technical = media.probe_video(source, cancel)
        frames, cuts, warnings = media.scan_video(source, technical, threshold, cancel, progress)
        selections = [media.select_keyframes(frames, start, end) for start, end in zip(cuts, cuts[1:])]
        images = media.extract_keyframes(source, bundle, [index for selection in selections for index in selection], technical, cancel, progress)
        for i, (start, end) in enumerate(zip(cuts, cuts[1:])):
            check_cancel(cancel)
            span = {"start_us": frames[start]["time_us"], "end_us": frames[end]["time_us"] if end < len(frames) else technical["duration_us"]}
            keyframes = [{"id": stable_id(sha, "frame", index), "time_us": frames[index]["time_us"],
                          "decode_index": index, "source_pts": frames[index]["source_pts"], **images[index]} for index in selections[i]]
            shot = _shot(sha, i + 1, span, keyframes, run, frames[start]["cut_score"] if start else None)
            ids = [frame["id"] for frame in keyframes]
            for field in ("brightness", "saturation", "sharpness"):
                shot["analysis"][field] = claim(round(float(np.mean([frame[field] for frame in frames[start:end]])), 5), "measured", None, ids, run["id"])
            frame = cv2.imdecode(np.fromfile(bundle / keyframes[len(keyframes) // 2]["uri"], dtype=np.uint8), cv2.IMREAD_COLOR)
            shot["analysis"]["dominant_colors"] = claim(media.dominant_colors(frame), "measured", None, [keyframes[len(keyframes) // 2]["id"]], run["id"])
            if len(keyframes) < 3:
                shot["warnings"].append("镜头不足 3 个解码帧，已保留全部可用帧；运动判断证据不足。")
            doc["shots"].append(shot)
        doc["warnings"].extend(warnings)
        doc["warnings"].append("自动镜头边界是全帧色彩差分候选；闪光/高速运动可能误切，渐变/叠化可能漏切，需按素材复核。")
        if technical["probe_method"] != "ffprobe":
            doc["warnings"].append("未找到 ffprobe，技术信息由 FFmpeg 日志和 OpenCV 补充；呈现时间来自 FFmpeg 解码 PTS。")
    doc["asset"]["technical"] = technical
    if technical.get("has_audio"):
        doc["warnings"].append("已检测到音轨；尚未进行语音、音乐或声音识别。")
    doc["warnings"].append("情绪、叙事用途与视觉语义需要显式启用视觉模型；AI 自评置信度未经校准。")
    run.update(status="completed", completed_at=now_iso())
    return doc


def _apply_vision(shot, result, run):
    for group in ("analysis", "emotion"):
        for field, item in result[group].items():
            if not _protected(shot[group].get(field, {})):
                shot[group][field] = claim(item["value"], "inferred", item["confidence"], item["evidence_ids"], run["id"])
    human = [region for region in shot["regions"] if region.get("origin") == "human"]
    ocr = [region for region in shot["regions"] if region.get("source") == "ocr"]
    shot["regions"] = human + ocr + [{"id": stable_id(shot["id"], "vision-region", i, region), **region} for i, region in enumerate(result["regions"])]
    shot["warnings"] = [warning for warning in shot["warnings"] if not warning.startswith(("AI 分析失败", "模型"))]
    shot["warnings"].extend(result["warnings"])
    shot["status"] = "analyzed"
    shot.setdefault("extensions", {})["vision_status"] = "abstained" if result.get("abstained") else "completed"


def _ocr_shot(shot, bundle, reader, run, cancel):
    texts, regions, evidence_ids = [], [], []
    for frame in shot["keyframes"]:
        check_cancel(cancel)
        image = cv2.imdecode(np.fromfile(bundle / frame["uri"], dtype=np.uint8), cv2.IMREAD_COLOR)
        result, _ = reader(image)
        check_cancel(cancel)
        if not result:
            continue
        evidence_ids.append(frame["id"])
        for box, text, score in result:
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()[:1000]
            if text not in texts:
                texts.append(text)
            points = np.asarray(box, dtype=float)
            left, top = np.clip(points.min(axis=0), (0, 0), (frame["width"], frame["height"]))
            right, bottom = np.clip(points.max(axis=0), (0, 0), (frame["width"], frame["height"]))
            if right <= left or bottom <= top:
                continue
            normalized = [left / frame["width"], top / frame["height"], (right - left) / frame["width"], (bottom - top) / frame["height"]]
            regions.append({"id": stable_id(shot["id"], "ocr", frame["id"], normalized, text), "keyframe_id": frame["id"],
                            "xywh": normalized, "label": text, "origin": "inferred", "confidence": providers._confidence(float(score)), "source": "ocr"})
    if not _protected(shot["analysis"].get("ocr_text", {})):
        shot["analysis"]["ocr_text"] = claim(texts or None, "inferred", None, evidence_ids, run["id"])
    shot["regions"] = [region for region in shot["regions"] if region.get("source") != "ocr" or region.get("origin") == "human"] + regions
    shot.setdefault("extensions", {})["ocr_completed"] = True


def analyze_asset(source: Path, output_root: Path, *, vision=False, provider_url=None, model=None, api_key=None,
                  threshold=27.0, resume=True, progress=None, cancel=None, ocr=False) -> Path:
    source = Path(source).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("找不到素材文件: " + str(source))
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 < threshold <= 255:
        raise ValueError("镜头检测 threshold 必须在 (0, 255] 内")
    if vision:
        providers.provider_endpoint(provider_url)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("启用 AI 需要指定视觉模型名称 model")
    if ocr and importlib.util.find_spec("rapidocr_onnxruntime") is None:
        raise ValueError("OCR 尚未安装；安装可选依赖 rapidocr-onnxruntime 后重试")
    initial_stat = source.stat()
    sha = media.sha256_file(source, cancel, progress)
    bundle = output_root / sha
    bundle.mkdir(parents=True, exist_ok=True)
    record = bundle / "record.fmp.json"
    with asset_lock(bundle):
        check_cancel(cancel)
        previous = read_document(record) if record.is_file() else None
        if previous:
            errors = validate_document(previous)
            if errors or previous["asset"]["source"]["sha256"] != sha:
                raise ValueError("已存在的记录无效，不能安全续跑；请另选输出目录或先修复记录")
        measurement_matches = previous and previous.get("extensions", {}).get("measurement_fingerprint") == _measurement_fingerprint(threshold)
        if resume and measurement_matches:
            doc = previous
            if any(not (bundle / frame["uri"]).is_file() for shot in doc["shots"] for frame in shot["keyframes"]):
                raise ValueError("续跑所需关键帧缺失；请使用新输出目录重新测量")
            doc["asset"]["source"]["uri"] = source.as_uri()
            doc["asset"]["name"] = source.name
            emit(progress, "resume", 1, 1, f"恢复记录 · {len(doc['shots'])} 个镜头")
        else:
            doc = _measure(source, bundle, sha, threshold, cancel, progress)
            if previous:
                doc["created_at"] = previous["created_at"]
                doc["history"] = deepcopy(previous.get("history", []))
                doc["history"].append({"at": now_iso(), "actor": "engine", "action": "remeasurement", "previous_measurement_fingerprint": previous.get("extensions", {}).get("measurement_fingerprint"), "before_shots": deepcopy(previous["shots"])})
                _preserve_reviewed(doc, previous)
                current_ids = {shot["id"] for shot in doc["shots"]}
                archived = list(previous.get("extensions", {}).get("archived_reviewed_shots", []))
                archived += [shot for shot in previous["shots"] if shot["id"] not in current_ids and (shot.get("review", {}).get("state") != "unreviewed" or any(_protected(value) for group in ("analysis", "emotion") for value in shot.get(group, {}).values()))]
                if archived:
                    doc["extensions"]["archived_reviewed_shots"] = archived
                    doc["warnings"].append("重新测量改变了镜头边界；不再匹配的人工记录保存在 extensions.archived_reviewed_shots 中，未自动重映射。")
            after = source.stat()
            if (after.st_size, after.st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
                raise RuntimeError("分析过程中源文件发生变化，请在文件写入完成后重试")
        _checkpoint(record, doc)
        if doc["asset"]["kind"] == "video":
            try:
                media.make_preview(source, bundle, doc["asset"]["technical"], cancel, progress)
            except AnalysisCancelled:
                raise
            except Exception as exc:
                doc["warnings"].append(f"浏览器预览未生成 ({type(exc).__name__})；原始分析记录已保留。")
                _checkpoint(record, doc)
        if ocr:
            from rapidocr_onnxruntime import RapidOCR
            reader = RapidOCR()
            run = _run("ocr", "rapidocr-onnxruntime", config={"local_only": True, "orientation": "display-corrected", "sampled_keyframes_only": True})
            doc["runs"].append(run)
            failed = 0
            try:
                for index, shot in enumerate(doc["shots"]):
                    check_cancel(cancel)
                    if shot["review"]["state"] in {"accepted", "edited", "rejected"} or (resume and shot.get("extensions", {}).get("ocr_completed")):
                        continue
                    emit(progress, "ocr", index + 1, len(doc["shots"]), f"本地文字识别 · 镜头 {index + 1}")
                    try:
                        _ocr_shot(shot, bundle, reader, run, cancel)
                    except AnalysisCancelled:
                        raise
                    except Exception as exc:
                        failed += 1
                        shot["warnings"].append(f"OCR 失败 ({type(exc).__name__})；可重试。")
                    _checkpoint(record, doc)
                run.update(status="partial" if failed else "completed", completed_at=now_iso(), failed_shots=failed)
            except AnalysisCancelled:
                run.update(status="cancelled", completed_at=now_iso())
                _checkpoint(record, doc)
                raise
            _checkpoint(record, doc)
        if vision:
            _, _, native = providers.provider_endpoint(provider_url)
            run = _run("vision", "ollama" if native else "openai-compatible", model,
                       {"provider_url": provider_url, "keyframes_per_shot": 5, "confidence_calibrated": False, "prompt_version": "director-compact-v3", "evidence_association": "all-submitted-keyframes-unless-model-cites-specific-frames", "context_limit": 8192 if native else None})
            doc["runs"].append(run)
            failed, done, skipped = 0, 0, 0
            try:
                for index, shot in enumerate(doc["shots"]):
                    check_cancel(cancel)
                    signature = _vision_fingerprint(shot, provider_url, model)
                    if shot["review"]["state"] in {"accepted", "edited", "rejected"} or (resume and shot["status"] == "analyzed" and shot.get("extensions", {}).get("vision_fingerprint") == signature):
                        if shot["status"] == "analyzed":
                            shot.setdefault("extensions", {}).setdefault("vision_status", "completed")
                        skipped += 1
                        continue
                    emit(progress, "vision", index + 1, len(doc["shots"]), f"视觉模型分析 · 镜头 {index + 1}/{len(doc['shots'])}")
                    try:
                        result = providers.analyze_shot(shot, bundle, provider_url, model, api_key, cancel=cancel)
                        check_cancel(cancel)
                        previous_ai = {group: {field: deepcopy(item) for field, item in shot[group].items() if item.get("origin") == "inferred" and item.get("value") is not None} for group in ("analysis", "emotion")}
                        if any(previous_ai.values()):
                            doc["history"].append({"at": now_iso(), "actor": "engine", "action": "vision-reanalysis", "shot_id": shot["id"], "run_id": run["id"], "before": previous_ai})
                        _apply_vision(shot, result, run)
                        shot.setdefault("extensions", {})["vision_fingerprint"] = signature
                        shot["extensions"]["vision_usage"] = result.get("usage", {})
                        done += 1
                    except AnalysisCancelled:
                        raise
                    except Exception as exc:
                        failed += 1
                        shot["status"] = "failed"
                        message = str(exc) if isinstance(exc, (providers.ProviderError, TimeoutError, ValueError)) else type(exc).__name__
                        shot["warnings"].append("AI 分析失败: " + message[:300])
                    _checkpoint(record, doc)
                run.update(status="failed" if failed and not done else "partial" if failed else "completed",
                           completed_at=now_iso(), analyzed_shots=done, failed_shots=failed, skipped_shots=skipped)
            except AnalysisCancelled:
                run.update(status="cancelled", completed_at=now_iso(), analyzed_shots=done, failed_shots=failed)
                _checkpoint(record, doc)
                raise
            _checkpoint(record, doc)
        abstained = sum(shot.get("extensions", {}).get("vision_status") == "abstained" for shot in doc["shots"])
        detail = "；部分 AI 镜头失败，可续跑" if any(shot["status"] == "failed" for shot in doc["shots"]) else f"；{abstained} 个镜头模型弃答，语义保持未知" if abstained else ""
        emit(progress, "complete", len(doc["shots"]), len(doc["shots"]), "记录已保存" + detail)
        return record
