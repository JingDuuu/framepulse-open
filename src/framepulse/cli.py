"""Machine-readable single-asset commands. Orchestrators own batch scheduling."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import provider_defaults
from .media import AnalysisCancelled
from .protocol import export_shots_csv, export_web_annotations, read_document, validate_document


def emit(value, *, error=False):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), file=sys.stderr if error else sys.stdout, flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="framepulse", description="FramePulse Open — 视频 / 图片的可验证标记中间体")
    result.add_argument("--version", action="version", version=__version__)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities", help="输出引擎与本地模型能力 JSON")
    analyze = sub.add_parser("analyze", help="分析一份媒体；重复调用自动续做")
    analyze.add_argument("source", type=Path)
    analyze.add_argument("--output", type=Path, default=Path("runtime/library"), help="旁车库输出目录")
    vision = analyze.add_mutually_exclusive_group()
    vision.add_argument("--vision", action="store_true", help="启用所配置的视觉模型")
    vision.add_argument("--no-vision", action="store_true", help="仅本地测量，不调用 AI")
    analyze.add_argument("--provider-url", help="OpenAI-compatible /v1 URL；显式搭配 --vision")
    analyze.add_argument("--model")
    analyze.add_argument("--profile", choices=["fast", "balanced", "detailed"], default="balanced")
    analyze.add_argument("--threshold", type=float, default=27.0)
    analyze.add_argument("--ocr", action="store_true", help="可选 RapidOCR 中文/英文证据帧文字识别")
    analyze.add_argument("--no-resume", action="store_true", help="禁用恢复；引擎仍应保护人工修订")
    validate = sub.add_parser("validate", help="JSON Schema + 时间与引用语义校验")
    validate.add_argument("record", type=Path)
    inspect = sub.add_parser("inspect", help="读取标准记录")
    inspect.add_argument("record", type=Path)
    inspect.add_argument("--shot", help="按稳定镜头 ID 读取一镜")
    search = sub.add_parser("search", help="搜索记录内的镜头语义与人工备注")
    search.add_argument("record", type=Path)
    search.add_argument("query")
    export = sub.add_parser("export", help="导出 JSON、CSV 或 W3C Web Annotation")
    export.add_argument("record", type=Path)
    export.add_argument("--format", choices=["json", "csv", "web-annotation"], default="json")
    export.add_argument("--output", type=Path)
    export.add_argument("--source-uri", help="W3C 主媒体 URI；不会替换证据帧 URI")
    export.add_argument("--evidence-base-uri", help="W3C 证据帧所在目录 URI，分享时与 --source-uri 一起指定")
    serve = sub.add_parser("serve", help="启动本机浏览和审核工作台")
    serve.add_argument("--data", type=Path, default=Path("runtime/library"))
    serve.add_argument("--port", type=int, default=47840)
    serve.add_argument("--open", action="store_true", help="在默认浏览器打开界面")
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "capabilities":
            from .engine import capabilities
            emit({"version": __version__, "protocol": "0.1.0", **capabilities(), "vision_provider": provider_defaults()})
        elif args.command == "analyze":
            from .engine import analyze_asset
            if not 1 <= args.threshold <= 100:
                raise ValueError("threshold 必须在 1 到 100 之间")
            config = provider_defaults()
            automatic_local = config["vision_available"] and config["provider_source"] == "local-discovery"
            vision = args.vision or (not args.no_vision and automatic_local and not args.provider_url)
            cancelled = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: cancelled.set())
            output = analyze_asset(args.source.resolve(), args.output.resolve(), vision=vision,
                                   provider_url=args.provider_url or config["provider_url"], model=args.model or config["model"],
                                   api_key=os.getenv("FRAMEPULSE_API_KEY"), threshold=args.threshold, resume=not args.no_resume,
                                   progress=lambda value: emit({"event": "progress", **value}, error=True),
                                   cancel=cancelled.is_set, ocr=args.ocr, profile=args.profile)
            doc = read_document(output)
            errors = validate_document(doc)
            if errors:
                emit({"event": "error", "code": "invalid_record", "errors": errors}, error=True)
                return 2
            failed = sum(s["status"] == "failed" for s in doc["shots"])
            ocr_failed = next((r.get("failed_shots", 0) for r in reversed(doc["runs"]) if r["kind"] == "ocr"), 0) if args.ocr else 0
            emit({"event": "result", "record": str(Path(output).resolve()), "asset_id": doc["asset"]["id"],
                  "shot_count": len(doc["shots"]), "failed_shots": failed, "failed_ocr_shots": ocr_failed, "vision": vision,
                  "status": "cancelled" if cancelled.is_set() else "partial" if failed or ocr_failed else "completed"})
            return 4 if cancelled.is_set() else 3 if failed or ocr_failed else 0
        elif args.command == "validate":
            errors = validate_document(read_document(args.record))
            emit({"valid": not errors, "errors": errors})
            return 2 if errors else 0
        elif args.command == "inspect":
            doc = read_document(args.record)
            result = next((s for s in doc["shots"] if s["id"] == args.shot), None) if args.shot else doc
            if result is None:
                raise ValueError("镜头 ID 不存在")
            emit(result)
        elif args.command == "search":
            doc = read_document(args.record)
            terms = args.query.casefold().split()
            matches = []
            for shot in doc["shots"]:
                text = json.dumps({"analysis": {k: v["value"] for k, v in shot["analysis"].items()},
                                   "review": shot["review"], "regions": shot["regions"]}, ensure_ascii=False).casefold()
                if all(term in text for term in terms):
                    matches.append({"id": shot["id"], "index": shot["index"], "range": shot["range"], "summary": shot["analysis"].get("summary", {}).get("value")})
            emit({"asset_id": doc["asset"]["id"], "matches": matches})
        elif args.command == "export":
            doc = read_document(args.record)
            errors = validate_document(doc)
            if errors:
                raise ValueError("记录校验失败：" + "; ".join(errors[:3]))
            if args.format == "csv":
                result = export_shots_csv(doc)
            else:
                result = json.dumps(export_web_annotations(doc, args.source_uri, args.evidence_base_uri or args.record.resolve().parent.as_uri() + "/") if args.format == "web-annotation" else doc, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(result, encoding="utf-8")
                emit({"output": str(args.output.resolve()), "format": args.format})
            else:
                print(result)
        elif args.command == "serve":
            import uvicorn
            from .server import create_app
            if not 1024 <= args.port <= 65535:
                raise ValueError("端口必须在 1024 到 65535 之间")
            app = create_app(args.data, token=os.getenv("FRAMEPULSE_SERVER_TOKEN"))
            if args.open:
                import webbrowser
                threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        return 0
    except (KeyboardInterrupt, AnalysisCancelled):
        emit({"event": "error", "code": "cancelled", "message": "已取消；已完成的镜头保留在旁车文件中"}, error=True)
        return 4
    except Exception as error:
        emit({"event": "error", "code": "failed", "message": str(error)}, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
