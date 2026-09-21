"""Bounded, local-only media decoding with presentation timestamps.

FFmpeg decodes every presented frame. Detection is a heuristic; time values are
rounded microseconds, while original integer PTS and the time base are retained.
"""
from __future__ import annotations

from collections import deque
from fractions import Fraction
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_PIXELS = 80_000_000
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class AnalysisCancelled(RuntimeError):
    """The caller cancelled; completed checkpoints remain usable."""


def check_cancel(cancel=None):
    if cancel and cancel():
        raise AnalysisCancelled("分析已取消；已完成的镜头记录已保留。")


def emit(progress, phase, current, total, message):
    if progress:
        progress({"phase": phase, "current": current, "total": total, "message": message})


def ffmpeg_executable():
    configured = os.environ.get("FRAMEPULSE_FFMPEG")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise ValueError("FRAMEPULSE_FFMPEG 指向的文件不存在")
        return str(path.resolve())
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ffprobe_executable():
    configured = os.environ.get("FRAMEPULSE_FFPROBE")
    if configured:
        return str(Path(configured).resolve()) if Path(configured).is_file() else None
    discovered = shutil.which("ffprobe")
    if discovered:
        return discovered
    sibling = Path(ffmpeg_executable()).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return str(sibling) if sibling.is_file() else None


def process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def run_process(args, *, cancel=None, timeout=60, tick=None):
    """Drain output with communicate, checking cancellation every 150 ms."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **process_options())
    started = time.monotonic()
    try:
        while True:
            check_cancel(cancel)
            if time.monotonic() - started > timeout:
                raise TimeoutError("媒体处理超过时间限制")
            try:
                out, err = process.communicate(timeout=0.15)
                if process.returncode:
                    detail = err.decode("utf-8", "replace")[-1500:]
                    raise RuntimeError(f"FFmpeg 处理失败 ({process.returncode}): {detail}")
                return out, err
            except subprocess.TimeoutExpired:
                if tick:
                    tick()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def sha256_file(source, cancel=None, progress=None):
    digest = hashlib.sha256()
    size = source.stat().st_size
    done = 0
    with source.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            check_cancel(cancel)
            digest.update(chunk)
            done += len(chunk)
            emit(progress, "hash", done, size, "计算素材指纹")
    return digest.hexdigest()


def _fraction(value):
    try:
        result = Fraction(str(value))
        return result if result > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def _rate_dict(value):
    fraction = _fraction(value)
    return {"numerator": fraction.numerator, "denominator": fraction.denominator} if fraction else None


def probe_video(source, cancel=None):
    ffprobe = ffprobe_executable()
    if ffprobe:
        out, _ = run_process([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(source)], cancel=cancel)
        data = json.loads(out)
        video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video" and not item.get("disposition", {}).get("attached_pic")), None)
        if video is None:
            raise ValueError("没有可分析的视频流")
        rotation = float(video.get("tags", {}).get("rotate", 0))
        for side in video.get("side_data_list", []):
            if "rotation" in side:
                rotation = float(side["rotation"])
        width, height = int(video["width"]), int(video["height"])
        if round(rotation) % 180:
            width, height = height, width
        duration = video.get("duration") or data.get("format", {}).get("duration")
        result = {"width": width, "height": height, "duration_us": round(float(duration) * 1_000_000) if duration else None,
                  "frame_rate": _rate_dict(video.get("avg_frame_rate")), "codec": video.get("codec_name"),
                  "has_audio": any(item.get("codec_type") == "audio" for item in data.get("streams", [])),
                  "time_base": video.get("time_base"), "rotation_degrees": rotation,
                  "probe_method": "ffprobe", "pixel_format": video.get("pix_fmt"),
                  "color_primaries": video.get("color_primaries"), "color_transfer": video.get("color_transfer"),
                  "color_space": video.get("color_space"), "sample_aspect_ratio": video.get("sample_aspect_ratio")}
    else:
        # FFmpeg's showinfo supplies exact stream PTS later. OpenCV is used only
        # for display dimensions and nominal rate in this fallback probe.
        _, err = run_process([ffmpeg_executable(), "-hide_banner", "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-an", "-f", "null", "-"], cancel=cancel)
        log = err.decode("utf-8", "replace")
        capture = cv2.VideoCapture(str(source))
        try:
            if not capture.isOpened():
                raise ValueError("无法打开视频；请检查格式或文件完整性")
            width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = capture.get(cv2.CAP_PROP_FPS)
            orientation = capture.get(cv2.CAP_PROP_ORIENTATION_META) if hasattr(cv2, "CAP_PROP_ORIENTATION_META") else 0
        finally:
            capture.release()
        duration = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", log)
        codec = re.search(r"Stream[^\n]*Video:\s*([^\s,(]+)", log)
        result = {"width": width, "height": height,
                  "duration_us": round((int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) * 1_000_000) if duration else None,
                  "frame_rate": _rate_dict(Fraction(fps).limit_denominator(100_000)) if fps and math.isfinite(fps) else None,
                  "codec": codec[1] if codec else None, "has_audio": bool(re.search(r"Stream[^\n]*Audio:", log)),
                  "time_base": None, "rotation_degrees": orientation, "probe_method": "ffmpeg-log+opencv"}
    if result["width"] <= 0 or result["height"] <= 0 or result["width"] * result["height"] > MAX_PIXELS:
        raise ValueError(f"素材尺寸异常或超过 {MAX_PIXELS:,} 像素上限")
    return result


def frame_metrics(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    brightness = float(np.mean(gray)) / 255
    saturation = float(np.mean(hsv[:, :, 1])) / 255
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    clipping = float(np.mean((gray < 8) | (gray > 247)))
    # Quality is a ranking heuristic, not a semantic assessment.
    quality = math.log1p(sharpness) - 2.5 * clipping - abs(brightness - 0.5)
    return {"brightness": round(brightness, 5), "saturation": round(saturation, 5),
            "sharpness": round(sharpness, 3), "quality": round(quality, 4)}


def dominant_colors(bgr, count=5):
    small = cv2.resize(bgr, (64, 64), interpolation=cv2.INTER_AREA)[:, :, ::-1]
    # Quantized RGB histogram is deterministic and does not hallucinate names.
    quantized = (small.reshape(-1, 3) // 32) * 32 + 16
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    order = np.argsort(-counts, kind="stable")[:count]
    return [{"hex": "#" + "".join(f"{int(c):02x}" for c in colors[i]), "share": round(int(counts[i]) / 4096, 4)} for i in order]


def scan_video(source, technical, threshold, cancel=None, progress=None):
    """Return every frame's PTS, low-resolution measurements, and hard cuts."""
    scale = min(1, 320 / max(technical["width"], technical["height"]))
    width = max(2, round(technical["width"] * scale / 2) * 2)
    height = max(2, round(technical["height"] * scale / 2) * 2)
    args = [ffmpeg_executable(), "-hide_banner", "-nostdin", "-copyts", "-i", str(source),
            "-map", "0:v:0", "-an", "-sn", "-vf", f"scale={width}:{height}:flags=area,showinfo",
            "-pix_fmt", "bgr24", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **process_options())
    raw_frames, timestamps = queue.Queue(maxsize=8), queue.Queue()
    stop = threading.Event()
    errors = deque(maxlen=12)
    info = {"time_base": None}
    frame_bytes = width * height * 3

    def read_frames():
        try:
            while not stop.is_set():
                data = bytearray()
                while len(data) < frame_bytes:
                    chunk = process.stdout.read(frame_bytes - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                if not data:
                    break
                if len(data) != frame_bytes:
                    errors.append("视频末尾帧数据不完整")
                    break
                while not stop.is_set():
                    try:
                        raw_frames.put(bytes(data), timeout=0.2)
                        break
                    except queue.Full:
                        continue
        finally:
            while not stop.is_set():
                try:
                    raw_frames.put(None, timeout=0.2)
                    break
                except queue.Full:
                    continue

    def read_info():
        for line in iter(process.stderr.readline, b""):
            text = line.decode("utf-8", "replace")
            base = re.search(r"config in time_base:\s*(\d+/\d+)", text)
            if base:
                info["time_base"] = base[1]
            match = re.search(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*([-\d.e+]+)", text)
            if match:
                duration = re.search(r"\bduration:\s*(-?\d+)", text)
                timestamps.put({"index": int(match[1]), "source_pts": int(match[2]), "pts_seconds": float(match[3]),
                                "duration_pts": int(duration[1]) if duration else None})
            elif "error" in text.lower() or "invalid" in text.lower():
                errors.append(text.strip()[:240])

    threads = [threading.Thread(target=read_frames, daemon=True), threading.Thread(target=read_info, daemon=True)]
    for thread in threads:
        thread.start()
    frames, cuts = [], [0]
    previous = None
    previous_histogram = None
    last_activity = time.monotonic()
    first_pts_us = None
    try:
        while True:
            check_cancel(cancel)
            if time.monotonic() - last_activity > 60:
                raise TimeoutError("视频解码 60 秒没有产生新帧")
            try:
                raw = raw_frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if raw is None:
                break
            last_activity = time.monotonic()
            try:
                timestamp = timestamps.get(timeout=5)
            except queue.Empty as exc:
                raise RuntimeError("解码器未提供帧 PTS；无法可靠标记时间") from exc
            if timestamp["index"] != len(frames):
                raise RuntimeError("视频帧与 PTS 不匹配，已停止以避免错误标记")
            base = _fraction(info["time_base"])
            pts_us = round(timestamp["source_pts"] * base * 1_000_000) if base else round(timestamp["pts_seconds"] * 1_000_000)
            if first_pts_us is None:
                first_pts_us = pts_us
            at_us = pts_us - first_pts_us
            if frames and at_us <= frames[-1]["time_us"]:
                raise ValueError("素材存在重复或倒退的呈现时间戳；请先无损重封装后重试")
            bgr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
            histogram = cv2.calcHist([bgr], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            cv2.normalize(histogram, histogram, alpha=1, norm_type=cv2.NORM_L1)
            histogram_delta = cv2.compareHist(histogram, previous_histogram, cv2.HISTCMP_BHATTACHARYYA) if previous_histogram is not None else 0.0
            score = 0.0
            if previous is not None:
                diff = np.abs(hsv - previous)
                diff[:, :, 0] = np.minimum(diff[:, :, 0], 180 - diff[:, :, 0]) * (255 / 90)
                # Hue becomes undefined at zero saturation, so weight it down.
                diff[:, :, 0] *= np.minimum(hsv[:, :, 1], previous[:, :, 1]) / 255
                score = float(np.mean(diff))
            metrics = frame_metrics(bgr)
            frames.append({"index": len(frames), "time_us": at_us, "source_pts": timestamp["source_pts"],
                           "duration_us": round(timestamp["duration_pts"] * base * 1_000_000) if timestamp["duration_pts"] and base else None,
                           "cut_score": round(score, 3), "histogram_delta": round(float(histogram_delta), 5), **metrics})
            previous = hsv
            previous_histogram = histogram
            if len(frames) % 30 == 0 or len(frames) == 1:
                emit(progress, "detect", at_us, technical.get("duration_us") or at_us + 1, f"逐帧检测 · 已测量 {len(frames)} 帧")
        code = process.wait(timeout=10)
        if code or not frames:
            raise RuntimeError("视频解码失败: " + ("; ".join(errors) or f"exit {code}"))
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
    intervals = [frames[i]["time_us"] - frames[i - 1]["time_us"] for i in range(1, len(frames))]
    rate = technical.get("frame_rate")
    fallback = round(1_000_000 * rate["denominator"] / rate["numerator"]) if rate else 40_000
    last_duration = frames[-1]["duration_us"] or (int(np.median(intervals[-120:])) if intervals else fallback)
    technical.update({"duration_us": frames[-1]["time_us"] + max(1, last_duration), "decoded_frames": len(frames),
                      "time_base": info["time_base"], "source_first_pts": frames[0]["source_pts"],
                      "source_first_pts_us": first_pts_us,
                      "timing_accuracy": "decoded_pts_rounded_to_microseconds", "timing_frame_exact": False,
                      "last_frame_duration_estimated": not bool(frames[-1]["duration_us"]),
                      "frame_rate_mode": "variable" if intervals and max(intervals) - min(intervals) > 4 else "constant"})
    # Camera/object movement can make pixel differences large on every frame.
    # Keep isolated changes and strong distribution changes, suppress sustained
    # high-motion differences with similar color distribution. This remains a
    # candidate detector, not a guarantee of editorial shot boundaries.
    cuts = [0]
    for index in range(1, len(frames)):
        score = frames[index]["cut_score"]
        if score < threshold:
            continue
        neighbors = [frames[j]["cut_score"] for j in range(max(1, index - 5), min(len(frames), index + 6)) if j != index]
        baseline = float(np.median(neighbors)) if neighbors else 0
        if frames[index]["histogram_delta"] >= 0.65 or score >= max(threshold, baseline * 1.8):
            cuts.append(index)
    cuts.append(len(frames))
    return frames, cuts, list(errors)


def select_keyframes(frames, start, end, count=5):
    """Pick the best exposed/sharp frame from each of five temporal bins."""
    subset = frames[start:end]
    if len(subset) <= count:
        return list(range(start, end))
    at_start, at_end = subset[0]["time_us"], subset[-1]["time_us"] + 1
    bins = [[] for _ in range(count)]
    for frame in subset:
        index = min(count - 1, int((frame["time_us"] - at_start) * count / (at_end - at_start)))
        bins[index].append(frame)
    selected = [max(items, key=lambda frame: (frame["quality"], -abs(frame["time_us"] - (at_start + (i + 0.5) * (at_end - at_start) / count))))["index"] for i, items in enumerate(bins) if items]
    # VFR can leave bins empty. Fill with other observed frames, never invent.
    for frame in sorted(subset, key=lambda frame: frame["quality"], reverse=True):
        if len(selected) >= min(count, len(subset)):
            break
        if frame["index"] not in selected:
            selected.append(frame["index"])
    return sorted(selected)


def extract_keyframes(source, bundle, indices, technical, cancel=None, progress=None):
    frame_dir = bundle / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    scale = min(1, 1280 / max(technical["width"], technical["height"]))
    width, height = max(2, round(technical["width"] * scale / 2) * 2), max(2, round(technical["height"] * scale / 2) * 2)
    ordered = sorted(set(indices))
    # A filter script avoids Windows' command-line length limit for long films.
    with tempfile.TemporaryDirectory(prefix="extract-", dir=bundle) as temp:
        temp = Path(temp)
        script = temp / "filter.txt"
        script.write_text("select='" + "+".join(f"eq(n,{i})" for i in ordered) + f"',scale={width}:{height}:flags=lanczos", encoding="utf-8")
        def tick():
            done = len(list(temp.glob("frame-*.jpg")))
            emit(progress, "keyframes", done, len(ordered), f"提取多帧证据 · {done}/{len(ordered)}")
        run_process([ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source), "-map", "0:v:0",
                     "-an", "-sn", "-filter_script:v", str(script), "-fps_mode", "vfr", "-q:v", "3", str(temp / "frame-%06d.jpg")],
                    cancel=cancel, timeout=max(120, technical["duration_us"] / 1_000_000 * 4), tick=tick)
        images = sorted(temp.glob("frame-*.jpg"))
        if len(images) != len(ordered):
            raise RuntimeError(f"关键帧数不匹配：期望 {len(ordered)}，实际 {len(images)}")
        result = {}
        for index, image in zip(ordered, images):
            destination = frame_dir / f"f-{index:09d}.jpg"
            image.replace(destination)
            result[index] = {"uri": destination.relative_to(bundle).as_posix(), "width": width, "height": height}
    return result


def prepare_image(source, bundle, cancel=None):
    check_cancel(cancel)
    with Image.open(source) as original:
        if original.width * original.height > MAX_PIXELS:
            raise ValueError(f"图片超过 {MAX_PIXELS:,} 像素上限")
        codec = original.format
        exif_orientation = original.getexif().get(274, 1)
        frames = getattr(original, "n_frames", 1)
        oriented = ImageOps.exif_transpose(original)
        has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
        visible_fraction = None
        if has_alpha:
            rgba = oriented.convert("RGBA")
            visible_fraction = float(np.mean(np.asarray(rgba.getchannel("A")) > 0))
            display = Image.alpha_composite(Image.new("RGBA", rgba.size, (128, 128, 128, 255)), rgba).convert("RGB")
        else:
            display = oriented.convert("RGB")
        technical = {"width": display.width, "height": display.height, "duration_us": None, "frame_rate": None,
                     "codec": codec, "has_audio": False, "time_base": None, "exif_orientation": exif_orientation,
                     "display_orientation_applied": True, "embedded_frame_count": frames, "probe_method": "pillow", "has_alpha": has_alpha}
        if has_alpha:
            technical.update(alpha_preview_background="#808080", visible_pixel_fraction=visible_fraction)
        display.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        frame_dir = bundle / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        destination = frame_dir / "image.jpg"
        display.save(destination, format="JPEG", quality=92)
        bgr = cv2.cvtColor(np.asarray(display), cv2.COLOR_RGB2BGR)
        metrics = frame_metrics(bgr)
        metrics["dominant_colors"] = dominant_colors(bgr)
        evidence = {"uri": "frames/image.jpg", "width": display.width, "height": display.height}
    warnings = ["多页/动态图片目前仅分析首帧，其他帧未分析。"] if frames > 1 else []
    if has_alpha:
        warnings.append("透明像素在证据图中合成到 #808080 中性灰底；灰底仅用于预览，不是素材内容。")
        if visible_fraction == 0:
            warnings.append("源图片全透明，没有有效可见像素；隐藏的 RGB 颜色不代表可见内容。")
    return technical, metrics, evidence, warnings


def make_preview(source, bundle, technical, cancel=None, progress=None):
    if source.suffix.lower() in {".mp4", ".m4v"} and technical.get("codec") in {"h264", "avc1"}:
        return None
    destination = bundle / "preview.mp4"
    if destination.is_file():
        return destination
    temporary = bundle / "preview.part.mp4"
    emit(progress, "preview", 0, 1, "生成可播放预览")
    try:
        run_process([ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
                     "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(temporary)],
                    cancel=cancel, timeout=max(180, technical["duration_us"] / 1_000_000 * 5))
        temporary.replace(destination)
        emit(progress, "preview", 1, 1, "播放预览已就绪")
        return destination
    finally:
        temporary.unlink(missing_ok=True)
