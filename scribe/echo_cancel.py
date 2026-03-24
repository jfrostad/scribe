"""Acoustic echo cancellation — remove speaker bleed from mic recording.

When using speakers (no headphones), the mic picks up remote participants'
audio through the speakers. This module subtracts that echo using NLMS
adaptive filtering, then uses energy-based gating to determine which
transcribed mic segments are actually the user speaking vs echo residue.

Processing pipeline:
1. Downsample both signals to 16kHz (what Whisper uses)
2. Run NLMS adaptive filter: mic = user_voice + echo(system)
3. Output the error signal (user voice with echo removed)
4. After transcription, gate mic segments by comparing cleaned vs original energy
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# NLMS parameters
FILTER_TAPS = 1600      # 100ms at 16kHz — covers room reverb
STEP_SIZE = 0.3          # adaptation rate
TARGET_SR = 16000        # Whisper's native sample rate


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample audio using polyphase filtering."""
    if orig_sr == target_sr:
        return audio
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(orig_sr, target_sr)
    return resample_poly(audio, target_sr // g, orig_sr // g)


def _nlms(mic: np.ndarray, ref: np.ndarray, M: int = FILTER_TAPS, mu: float = STEP_SIZE) -> np.ndarray:
    """NLMS adaptive filter — estimate and subtract echo from mic.

    Args:
        mic: Microphone signal (contains user voice + echo)
        ref: Reference signal (system audio = echo source)
        M: Filter length in samples
        mu: Step size (0 < mu <= 1)

    Returns:
        Error signal (mic with echo subtracted)
    """
    n = len(mic)
    eps = 1e-6
    w = np.zeros(M)
    out = np.zeros(n)

    # Pre-pad reference for indexing
    ref_padded = np.concatenate([np.zeros(M), ref])

    for i in range(n):
        x = ref_padded[i:i + M][::-1]
        y_hat = w @ x
        e = mic[i] - y_hat
        norm = x @ x + eps
        w += (mu * e / norm) * x
        out[i] = e

    return out


def cancel_echo(mic_path: Path, sys_path: Path, output_path: Path) -> Path:
    """Run echo cancellation on recorded audio files.

    Reads mic.wav and system.wav, runs NLMS echo cancellation,
    writes cleaned mic audio to output_path at 16kHz.

    Returns:
        Path to the cleaned audio file.
    """
    logger.info("Loading audio for echo cancellation...")
    mic, mic_sr = sf.read(str(mic_path))
    sys_audio, sys_sr = sf.read(str(sys_path))

    # Downsample to 16kHz
    mic_16 = _resample(mic, mic_sr, TARGET_SR)
    sys_16 = _resample(sys_audio, sys_sr, TARGET_SR)

    # Trim to same length
    n = min(len(mic_16), len(sys_16))
    mic_16 = mic_16[:n]
    sys_16 = sys_16[:n]

    logger.info(f"Running NLMS echo cancellation ({n / TARGET_SR:.0f}s of audio)...")
    cleaned = _nlms(mic_16, sys_16)

    # Normalize to prevent clipping
    peak = np.max(np.abs(cleaned))
    if peak > 0.95:
        cleaned = cleaned * (0.95 / peak)

    sf.write(str(output_path), cleaned.astype(np.float32), TARGET_SR)
    logger.info(f"Echo-cancelled audio saved to {output_path}")

    return output_path


def gate_segments(
    segments: list[dict],
    mic_path: Path,
    cleaned_path: Path,
    energy_threshold: float = 0.3,
) -> list[dict]:
    """Reassign mic segments that are just echo residue to 'Remote'.

    For each segment labeled 'You', compare the energy in the cleaned
    (echo-cancelled) signal vs the original mic. If the cleaned signal
    has very low energy relative to the original, the segment was echo
    bleed, not actual user speech.

    Args:
        segments: Timeline segments with 'speaker', 'start', 'end', 'text'
        mic_path: Path to original mic.wav
        cleaned_path: Path to echo-cancelled audio (16kHz)
        energy_threshold: Ratio of cleaned/original RMS below which
                          a segment is reassigned to 'Remote'

    Returns:
        Updated segments with corrected speaker labels.
    """
    cleaned, cleaned_sr = sf.read(str(cleaned_path))
    mic, mic_sr = sf.read(str(mic_path))

    # Resample mic to match cleaned (16kHz)
    if mic_sr != cleaned_sr:
        mic_16 = _resample(mic, mic_sr, cleaned_sr)
    else:
        mic_16 = mic

    n = min(len(mic_16), len(cleaned))
    reassigned = 0

    for seg in segments:
        if seg.get("speaker") != "You":
            continue

        start_sample = int(seg["start"] * cleaned_sr)
        end_sample = int(seg["end"] * cleaned_sr)
        start_sample = max(0, min(start_sample, n - 1))
        end_sample = max(start_sample + 1, min(end_sample, n))

        mic_chunk = mic_16[start_sample:end_sample]
        cleaned_chunk = cleaned[start_sample:end_sample]

        mic_rms = np.sqrt(np.mean(mic_chunk ** 2)) + 1e-10
        cleaned_rms = np.sqrt(np.mean(cleaned_chunk ** 2))

        ratio = cleaned_rms / mic_rms

        if ratio < energy_threshold:
            seg["speaker"] = "Remote"
            reassigned += 1

    if reassigned > 0:
        logger.info(f"Reassigned {reassigned} echo-bleed segments from 'You' to 'Remote'")

    return segments
