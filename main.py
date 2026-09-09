from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import AppConfig, ConfigError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trợ lý giọng nói tiếng Việt chạy hoàn toàn cục bộ")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-audio-devices", action="store_true")
    group.add_argument("--test-mic", action="store_true")
    group.add_argument("--test-tts", nargs="?", const="Xin chào, hệ thống đọc tiếng Việt đang hoạt động.")
    group.add_argument("--test-asr", type=Path, metavar="WAV")
    group.add_argument("--test-llm", nargs="?", const="Xin chào, hãy trả lời một câu ngắn.")
    group.add_argument("--web", action="store_true")
    return parser


async def async_main(args: argparse.Namespace, config: AppConfig) -> None:
    if args.web:
        from app.web.server import serve_web

        await serve_web(config)
        return

    if args.list_audio_devices:
        from app.audio.input import list_audio_devices

        list_audio_devices()
        return

    if args.test_mic:
        from app.diagnostics import test_microphone

        await test_microphone(config)
        return

    if args.test_tts is not None:
        from app.diagnostics import test_tts

        await test_tts(config, args.test_tts)
        return

    if args.test_asr is not None:
        from app.diagnostics import test_asr

        await test_asr(config, args.test_asr)
        return

    if args.test_llm is not None:
        from app.diagnostics import test_llm

        await test_llm(config, args.test_llm)
        return

    from app.orchestrator import VoiceOrchestrator

    orchestrator = VoiceOrchestrator(config)
    await orchestrator.run()


def main() -> int:
    _configure_console()
    args = build_parser().parse_args()
    try:
        config = AppConfig.load(args.config)
        asyncio.run(async_main(args, config))
    except (ConfigError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[APP] Đã dừng.")
    return 0


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
