"""Merge mic + system transcripts into a speaker-labeled timeline.

Channel-based diarization: mic = "You", system = "Remote".
Segments are sorted chronologically with overlap tolerance.
"""

from __future__ import annotations


def merge_transcripts(
    mic_result: dict,
    system_result: dict,
    overlap_tolerance: float = 0.5,
) -> list[dict]:
    """Merge two transcription results into a unified speaker-labeled timeline.

    Each entry: {"start": float, "end": float, "speaker": str, "text": str}
    """
    timeline = []

    for seg in mic_result.get("segments", []):
        timeline.append({
            "start": seg["start"],
            "end": seg["end"],
            "speaker": "You",
            "text": seg["text"],
        })

    for seg in system_result.get("segments", []):
        timeline.append({
            "start": seg["start"],
            "end": seg["end"],
            "speaker": "Remote",
            "text": seg["text"],
        })

    # Sort by start time
    timeline.sort(key=lambda s: s["start"])

    # Merge consecutive segments from the same speaker within overlap tolerance
    merged = []
    for seg in timeline:
        if (
            merged
            and merged[-1]["speaker"] == seg["speaker"]
            and seg["start"] - merged[-1]["end"] < overlap_tolerance
        ):
            merged[-1]["end"] = seg["end"]
            merged[-1]["text"] += " " + seg["text"]
        else:
            merged.append(dict(seg))

    return merged
