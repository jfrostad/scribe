"""Watch for active Zoom/Teams calls and auto-record.

Detection strategy:
- Zoom: look for CptHost process (only exists during active calls)
- Teams: check if the SlimCore media module has connected UDP sockets
  (idle Teams has a single unconnected UDP listener; active calls open
  connected UDP streams to media relay servers)

Polls every few seconds. When a call is detected, starts recording.
When the call ends, stops recording and kicks off transcribe + analyze.
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import date
from pathlib import Path

from scribe.config import AppConfig

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds


def _detect_zoom_call() -> bool:
    """Check if a Zoom call is active (CptHost process exists)."""
    result = subprocess.run(
        ["pgrep", "-f", "CptHost"],
        capture_output=True,
    )
    return result.returncode == 0


def _detect_teams_call() -> bool:
    """Check if Teams has an active call via SlimCore connected UDP sockets.

    When idle, SlimCore has ~1 unconnected UDP socket (listening on *:port).
    During a call, it opens multiple connected UDP sockets to media relays,
    shown in lsof as UDP connections with a remote address (->).
    """
    # Get SlimCore PID
    pgrep = subprocess.run(
        ["pgrep", "-f", "SlimCore"],
        capture_output=True,
        text=True,
    )
    if pgrep.returncode != 0:
        return False

    pid = pgrep.stdout.strip().split("\n")[0]

    # Check SlimCore's own FDs for connected UDP sockets
    # (have "->" indicating a remote peer for media streaming)
    lsof = subprocess.run(
        ["lsof", "-p", pid],
        capture_output=True,
        text=True,
    )
    for line in lsof.stdout.splitlines():
        if "UDP" in line and "->" in line:
            return True
    return False


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
            logger.error(f"Failed to start recording: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        # Wait for the call to end
        while _detect_active_call() is not None:
            time.sleep(POLL_INTERVAL)

        # Call ended — stop recording
        recorder.stop()
        logger.info(f"Call ended — recording saved to {session_dir}")

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

    if mic_path.exists():
        mic_result = transcribe(mic_path, config.whisper)
        logger.info(f"  Mic: {len(mic_result['words'])} words")

    if sys_path.exists():
        sys_result = transcribe(sys_path, config.whisper)
        logger.info(f"  System: {len(sys_result['words'])} words")

    timeline = merge_transcripts(mic_result, sys_result)
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
