"""Local-only transcription using faster-whisper.

Privacy: no audio or raw transcript ever leaves the machine.
"""

from __future__ import annotations

import logging
from pathlib import Path

from scribe.config import WhisperConfig

logger = logging.getLogger(__name__)

# Lazy-loaded model singleton
_model = None


def _get_model(config: WhisperConfig):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info(f"Loading whisper model: {config.model_size} (device={config.device})")
        _model = WhisperModel(
            config.model_size,
            device=config.device,
            compute_type=config.compute_type,
        )
    return _model


def transcribe(audio_path: Path, config: WhisperConfig) -> dict:
    """Transcribe an audio file locally.

    Returns:
        {"text": str, "language": str, "confidence": float,
         "segments": list, "words": list}
    """
    model = _get_model(config)

    segments, info = model.transcribe(
        str(audio_path),
        language=config.language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
    )

    all_segments = []
    all_words = []
    full_text_parts = []
    for seg in segments:
        all_segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())
        if seg.words:
            for w in seg.words:
                all_words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                    "probability": w.probability,
                })

    full_text = " ".join(full_text_parts)

    logger.info(
        f"Transcribed {audio_path.name}: {len(full_text)} chars, "
        f"{len(all_words)} words, "
        f"lang={info.language} ({info.language_probability:.2f})"
    )

    return {
        "text": full_text,
        "language": info.language,
        "confidence": info.language_probability,
        "segments": all_segments,
        "words": all_words,
    }
