"""Render merged timeline to markdown transcript + JSON sidecar."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from scribe.utils import format_timestamp, format_duration


def render_markdown(
    timeline: list[dict],
    session_name: str,
    date: str,
    duration_secs: float,
) -> str:
    """Render a speaker-labeled timeline to markdown."""
    lines = [
        f"# {session_name}",
        f"**Date:** {date}",
        f"**Duration:** {format_duration(duration_secs)}",
        "",
        "---",
        "",
    ]

    for seg in timeline:
        ts = format_timestamp(seg["start"])
        lines.append(f"**[{ts}] {seg['speaker']}:**")
        lines.append(seg["text"])
        lines.append("")

    return "\n".join(lines)


def write_transcript(
    timeline: list[dict],
    mic_result: dict,
    system_result: dict,
    session_dir: Path,
    session_name: str,
    duration_secs: float,
) -> tuple[Path, Path]:
    """Write transcript.md and transcript.meta.json to session directory."""
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Markdown
    md = render_markdown(timeline, session_name, date_str, duration_secs)
    md_path = session_dir / "transcript.md"
    md_path.write_text(md)

    # JSON sidecar
    meta = {
        "session_name": session_name,
        "date": date_str,
        "duration_secs": duration_secs,
        "timeline": timeline,
        "mic": {
            "text": mic_result.get("text", ""),
            "language": mic_result.get("language", ""),
            "confidence": mic_result.get("confidence", 0),
            "segments": mic_result.get("segments", []),
            "words": mic_result.get("words", []),
        },
        "system": {
            "text": system_result.get("text", ""),
            "language": system_result.get("language", ""),
            "confidence": system_result.get("confidence", 0),
            "segments": system_result.get("segments", []),
            "words": system_result.get("words", []),
        },
    }
    json_path = session_dir / "transcript.meta.json"
    json_path.write_text(json.dumps(meta, indent=2))

    return md_path, json_path
