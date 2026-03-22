"""Audio helpers — device listing, timestamps, mixing."""

from __future__ import annotations

import sounddevice as sd


def list_devices() -> str:
    """Return a formatted string of all audio devices."""
    devices = sd.query_devices()
    lines = []
    for i, d in enumerate(devices):
        direction = []
        if d["max_input_channels"] > 0:
            direction.append(f"in:{d['max_input_channels']}ch")
        if d["max_output_channels"] > 0:
            direction.append(f"out:{d['max_output_channels']}ch")
        marker = " *" if d["name"] == sd.query_devices(kind="input")["name"] else ""
        lines.append(f"  [{i}] {d['name']} ({', '.join(direction)}){marker}")
    return "\n".join(lines)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_duration(seconds: float) -> str:
    """Format duration as human-readable string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
