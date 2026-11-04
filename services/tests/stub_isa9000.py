from __future__ import annotations

import json
import os
import struct
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

import lameenc
from isa9000.cli import build_parser
from isa9000.config import load_config
from isa9000.progress import Step


def _emit(stage: str, message: str, **fields) -> None:
    print(Step(stage, message, **fields).to_json(), flush=True)
    time.sleep(float(os.environ.get("ISA9000_STUB_STEP_DELAY", "0.1")))


def _save(folder: Path, manifest: dict) -> None:
    (folder / "podcast.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command not in {"create", "record"}:
        parser.error("The local stub supports create and record only.")
    code = int(os.environ.get("ISA9000_STUB_EXIT", "0"))
    if code not in {0, 1, 3}:
        parser.error("ISA9000_STUB_EXIT must be 0, 1 or 3.")
    config = load_config(args.config)
    _emit("check", "Checking local demo sources")
    if code == 1:
        _emit("error", "Demo source could not be read.\nRetry with ISA9000_STUB_EXIT=0.")
        print("isa9000: Demo source could not be read.", file=sys.stderr)
        return 1

    if args.command == "create":
        root = Path(args.audio_kb) if args.audio_kb else config.output_folder
        folder = root / "Demo course"
        folder.mkdir(parents=True, exist_ok=True)
        _emit("plan", "Planning the demo course", folder=str(folder), total=2)
        manifest = {
            "title": "Welcome to the learning library",
            "description": "A local demonstration with silent audio and readable transcripts.",
            "request": args.request,
            "sources": args.sources,
            "read": args.sources,
            "created": datetime.now(UTC).isoformat(),
            "tags": ["Demo", "Onboarding"],
            "cover": "cover.png",
            "review": {
                "approved": code == 0,
                "fact_checked": True,
                "notes": ["Read the demo transcripts before recording."] if code == 3 else [],
            },
            "episodes": [],
        }
        for number, title in enumerate(("Getting started", "Working together"), 1):
            name = f"{number:02d} - {title}.txt"
            text = (
                f"Welcome to {config.branding.brand}. This is {title}.\n\n"
                "Your library contains courses shared with your group. "
                "Open a course to listen to its episodes or read their transcripts.\n\n"
                "This local demonstration uses silent audio and calls no AI service.\n"
            )
            (folder / name).write_text(text, encoding="utf-8")
            manifest["episodes"].append({
                "number": number,
                "title": title,
                "summary": "Learn how to use your team's library.",
                "transcript": name,
                "audio": None,
                "seconds": None,
                "score": 8 if code == 3 else 9,
                "issues": "Review the demo transcript." if code == 3 else "",
                "unverified": [],
            })
            _emit("write", "Writing the demo transcript", current=number, total=2)
        pixels = (b"\x00" + bytes((108, 60, 225)) * 64) * 64
        (folder / "cover.png").write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(pixels))
            + _chunk(b"IEND", b"")
        )
        _save(folder, manifest)
        _emit("review", "Reviewing the demo course", folder=str(folder))
    else:
        folder = Path(args.folder)
        manifest = json.loads((folder / "podcast.json").read_text(encoding="utf-8"))

    if code == 3:
        _emit("done", "Demo course needs review", folder=str(folder))
        print("isa9000: needs review before recording", file=sys.stderr)
        return 3
    if args.command == "create" and args.text_only:
        _emit("done", "Demo transcripts are ready", folder=str(folder))
        return 0

    for episode in manifest["episodes"]:
        audio = Path(episode["transcript"]).with_suffix(".mp3").name
        if episode.get("audio") and (folder / episode["audio"]).is_file():
            continue
        _emit("record", "Recording silent demo audio", current=episode["number"],
              total=len(manifest["episodes"]), folder=str(folder))
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(64)
        encoder.set_in_sample_rate(16000)
        encoder.set_channels(1)
        encoder.set_quality(2)
        (folder / audio).write_bytes(encoder.encode(b"\x00" * 32000) + encoder.flush())
        episode.update(audio=audio, seconds=1.0)
        _save(folder, manifest)
    _emit("done", "Demo course is ready", folder=str(folder))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
