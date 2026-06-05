"""Watch for active Zoom/Teams calls and auto-record.

Detection strategy:
- Zoom: look for CptHost process (only exists during active calls)
- Teams: count UDP sockets on the main MSTeams process. Idle Teams has
  ~1 UDP socket; an active call opens 10+ for media relay. Threshold
  of 4 avoids false positives.

Uses a grace period (consecutive "no call" polls) before stopping,
to handle brief detection gaps mid-call.

Polls every few seconds. When a call is detected, starts recording.
When the call ends, stops recording and kicks off transcribe + analyze.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

from scribe.config import AppConfig

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds
# Require this many consecutive "no call" polls before declaring call ended
END_GRACE_POLLS = 6  # 30 seconds grace period
# Idle Teams has ~1 UDP socket; active call has 10+
TEAMS_UDP_THRESHOLD = 4
# After this many consecutive failed recording starts, back off exponentially
# instead of retrying every POLL_INTERVAL. A persistently unavailable audio
# device previously caused a tight retry loop that exhausted file descriptors.
FAILURE_BACKOFF_THRESHOLD = 3
FAILURE_BACKOFF_MAX = 300  # cap backoff at 5 minutes


def _detect_zoom_call() -> bool:
    """Check if a Zoom call is active (CptHost process exists)."""
    result = subprocess.run(
        ["pgrep", "-f", "CptHost"],
        capture_output=True,
    )
    return result.returncode == 0


def _detect_teams_call() -> bool:
    """Check if Teams has an active call via UDP socket count.

    The main MSTeams process opens many UDP sockets for media relay
    during a call (~10-15). When idle, it has ~1.
    """
    result = subprocess.run(
        ["pgrep", "-x", "MSTeams"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False

    pid = result.stdout.strip().split("\n")[0]

    lsof = subprocess.run(
        ["/usr/sbin/lsof", "-i", "UDP", "-a", "-p", pid],
        capture_output=True,
        text=True,
    )
    udp_count = sum(1 for line in lsof.stdout.splitlines() if "UDP" in line)
    return udp_count >= TEAMS_UDP_THRESHOLD


def _detect_active_call() -> str | None:
    """Return the name of the active call app, or None."""
    if _detect_zoom_call():
        return "zoom"
    if _detect_teams_call():
        return "teams"
    return None


def _make_session_name(app: str) -> str:
    """Generate a session directory name."""
    timestamp = time.strftime("%H%M")
    return f"{date.today().isoformat()}_{app}-{timestamp}"


def watch(config: AppConfig, transcribe_after: bool = True, analyze_after: bool = True) -> None:
    """Main watch loop — detect calls, record, then process."""
    from scribe.recorder import DualRecorder

    logger.info("Watching for Zoom/Teams calls...")
    logger.info(f"  Poll interval: {POLL_INTERVAL}s")
    logger.info(f"  Auto-transcribe: {transcribe_after}")
    logger.info(f"  Auto-analyze: {analyze_after}")

    consecutive_failures = 0

    while True:
        # Wait for a call to start
        app = _detect_active_call()
        if app is None:
            time.sleep(POLL_INTERVAL)
            continue

        # Call detected — start recording
        session_name = _make_session_name(app)
        session_dir = config.output.base_path / session_name
        session_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Call detected ({app}) — recording to {session_dir}")

        recorder = DualRecorder(config.audio, session_dir)
        try:
            recorder.start()
        except Exception as e:
            consecutive_failures += 1
            logger.error(
                f"Failed to start recording (attempt {consecutive_failures}): {e}"
            )
            # The recorder releases its own handles on a failed start; just
            # drop the empty session dir so we don't litter recordings/.
            shutil.rmtree(session_dir, ignore_errors=True)

            if consecutive_failures >= FAILURE_BACKOFF_THRESHOLD:
                backoff = min(
                    FAILURE_BACKOFF_MAX,
                    POLL_INTERVAL * 2 ** (consecutive_failures - FAILURE_BACKOFF_THRESHOLD + 1),
                )
                logger.warning(
                    f"{consecutive_failures} consecutive recording failures; "
                    f"backing off {backoff}s (audio device may be unavailable)"
                )
                time.sleep(backoff)
            else:
                time.sleep(POLL_INTERVAL)
            continue

        # Recording started cleanly — reset the failure backoff.
        consecutive_failures = 0

        # Wait for the call to end (with grace period to handle flickers)
        no_call_count = 0
        while True:
            time.sleep(POLL_INTERVAL)
            if _detect_active_call() is not None:
                no_call_count = 0
            else:
                no_call_count += 1
                if no_call_count >= END_GRACE_POLLS:
                    break
                logger.debug(f"No call detected ({no_call_count}/{END_GRACE_POLLS}), waiting...")

        # Call ended — stop recording
        recorder.stop()
        logger.info(f"Call ended — recording saved to {session_dir}")

        # Check minimum recording duration (skip false triggers)
        import soundfile as sf
        mic_path = session_dir / "mic.wav"
        if mic_path.exists():
            info = sf.info(str(mic_path))
            if info.duration < 60:
                logger.info(f"Recording too short ({info.duration:.0f}s < 60s), cleaning up")
                shutil.rmtree(session_dir)
                continue

        # Post-processing
        if transcribe_after:
            _auto_transcribe(session_dir, config)

        if analyze_after and config.env.anthropic_api_key:
            _auto_analyze(session_dir, config)

        logger.info(f"Session complete: {session_dir}")
        logger.info("Watching for next call...")


def _auto_transcribe(session_dir: Path, config: AppConfig) -> None:
    """Run transcription on a completed recording."""
    from scribe.transcriber import transcribe
    from scribe.diarize import merge_transcripts
    from scribe.formatter import write_transcript

    logger.info("Transcribing...")

    mic_result = {"text": "", "segments": [], "words": []}
    sys_result = {"text": "", "segments": [], "words": []}

    mic_path = session_dir / "mic.wav"
    sys_path = session_dir / "system.wav"

    # Echo cancellation if both streams exist
    cleaned_path = None
    if mic_path.exists() and sys_path.exists():
        from scribe.echo_cancel import cancel_echo
        logger.info("Running echo cancellation...")
        cleaned_path = session_dir / "mic_cleaned.wav"
        try:
            cancel_echo(mic_path, sys_path, cleaned_path)
        except Exception as e:
            logger.warning(f"Echo cancellation failed: {e}, using raw mic")
            cleaned_path = None

    if mic_path.exists():
        transcribe_path = cleaned_path if cleaned_path else mic_path
        mic_result = transcribe(transcribe_path, config.whisper)
        logger.info(f"  Mic: {len(mic_result['words'])} words")

    if sys_path.exists():
        sys_result = transcribe(sys_path, config.whisper)
        logger.info(f"  System: {len(sys_result['words'])} words")

    timeline = merge_transcripts(mic_result, sys_result)

    # Gate echo-bleed segments
    if cleaned_path and cleaned_path.exists():
        from scribe.echo_cancel import gate_segments
        timeline = gate_segments(timeline, mic_path, cleaned_path)

    duration = max((seg["end"] for seg in timeline), default=0.0)

    # Extract display name from session dir
    display_name = session_dir.name
    if "_" in display_name:
        display_name = display_name.split("_", 1)[1]

    md_path, _ = write_transcript(
        timeline, mic_result, sys_result,
        session_dir, display_name, duration,
    )
    logger.info(f"  Transcript: {md_path}")

    if config.compression.enabled:
        from scribe.compress import compress_session_audio
        compress_session_audio(session_dir, config.compression)


def _auto_analyze(session_dir: Path, config: AppConfig) -> None:
    """Run Claude analysis on a transcript."""
    from scribe.analyze import analyze_transcript

    transcript_path = session_dir / "transcript.md"
    if not transcript_path.exists():
        logger.warning("No transcript to analyze")
        return

    logger.info("Analyzing...")
    output_path = analyze_transcript(session_dir, config)
    logger.info(f"  Analysis: {output_path}")
