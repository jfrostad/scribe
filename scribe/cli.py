"""Scribe CLI — local meeting transcription & analysis."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import date
from pathlib import Path

from scribe.config import load_config

logger = logging.getLogger("scribe")


def cmd_devices(args: argparse.Namespace) -> None:
    """List audio input devices."""
    from scribe.utils import list_devices

    print("Audio devices:")
    print(list_devices())


def cmd_setup(args: argparse.Namespace) -> None:
    """Print BlackHole setup instructions."""
    print("""BlackHole Setup Instructions
============================

1. Install BlackHole 2ch:
   brew install blackhole-2ch

2. Open Audio MIDI Setup:
   open -a "Audio MIDI Setup"

3. Create a Multi-Output Device:
   - Click '+' at bottom left → Create Multi-Output Device
   - Check your speakers/headphones AND BlackHole 2ch
   - Make sure your speakers are the top (master) device

4. Set the Multi-Output Device as your system output:
   System Settings → Sound → Output → Multi-Output Device

5. Verify in config.yaml:
   audio:
     system_device: "BlackHole 2ch"

This routes system audio to both your speakers AND BlackHole,
which Scribe captures as the "Remote" audio stream.

For Teams-only meetings, you may use "Microsoft Teams Audio"
as the system_device instead.
""")


def cmd_record(args: argparse.Namespace) -> None:
    """Start recording mic + system audio."""
    from scribe.recorder import DualRecorder

    config = load_config()
    name = args.name or "meeting"
    session_name = f"{date.today().isoformat()}_{name}"
    session_dir = config.output.base_path / session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    recorder = DualRecorder(config.audio, session_dir)

    # Handle Ctrl+C gracefully
    def handle_signal(sig, frame):
        print("\nStopping recording...")
        recorder.stop()
        print(f"Session saved: {session_dir}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Recording to: {session_dir}")
    print("Press Ctrl+C to stop.\n")

    try:
        recorder.start()
    except Exception as e:
        print(f"Error starting recording: {e}", file=sys.stderr)
        print("\nRun 'scribe devices' to check available audio devices.")
        sys.exit(1)

    # Block until signal
    signal.pause()


def cmd_transcribe(args: argparse.Namespace) -> None:
    """Transcribe a recorded session."""
    from scribe.transcriber import transcribe
    from scribe.diarize import merge_transcripts
    from scribe.formatter import write_transcript

    config = load_config()
    session_dir = Path(args.session_dir).resolve()

    mic_path = session_dir / "mic.wav"
    sys_path = session_dir / "system.wav"

    if not mic_path.exists() and not sys_path.exists():
        print(f"No audio files found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    print("Transcribing...")

    mic_result = {"text": "", "segments": [], "words": []}
    sys_result = {"text": "", "segments": [], "words": []}

    if mic_path.exists():
        print(f"  Mic audio: {mic_path.name}")
        mic_result = transcribe(mic_path, config.whisper)
        print(f"    → {len(mic_result['words'])} words")

    if sys_path.exists():
        print(f"  System audio: {sys_path.name}")
        sys_result = transcribe(sys_path, config.whisper)
        print(f"    → {len(sys_result['words'])} words")

    # Merge into timeline
    timeline = merge_transcripts(mic_result, sys_result)

    # Calculate duration from last segment end
    duration = 0.0
    if timeline:
        duration = max(seg["end"] for seg in timeline)

    # Extract session name from directory
    session_name = session_dir.name
    if "_" in session_name:
        # Strip date prefix: "2026-03-20_meeting" → "meeting"
        session_name = session_name.split("_", 1)[1]

    md_path, json_path = write_transcript(
        timeline, mic_result, sys_result,
        session_dir, session_name, duration,
    )

    print(f"\nTranscript written:")
    print(f"  {md_path}")
    print(f"  {json_path}")
    print(f"  {len(timeline)} segments, {duration:.0f}s total")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run Claude analysis on a transcript."""
    from scribe.analyze import analyze_transcript

    config = load_config()
    session_dir = Path(args.session_dir).resolve()

    if not (session_dir / "transcript.md").exists():
        print("No transcript.md found. Run 'scribe transcribe' first.", file=sys.stderr)
        sys.exit(1)

    print("Analyzing transcript...")
    output_path = analyze_transcript(session_dir, config)
    print(f"Analysis written to: {output_path}")


def cmd_run(args: argparse.Namespace) -> None:
    """Record → transcribe → analyze in one shot."""
    from scribe.recorder import DualRecorder
    from scribe.transcriber import transcribe
    from scribe.diarize import merge_transcripts
    from scribe.formatter import write_transcript
    from scribe.analyze import analyze_transcript

    config = load_config()
    name = args.name or "meeting"
    session_name = f"{date.today().isoformat()}_{name}"
    session_dir = config.output.base_path / session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    recorder = DualRecorder(config.audio, session_dir)

    # Phase 1: Record
    stopped = False

    def handle_signal(sig, frame):
        nonlocal stopped
        if not stopped:
            stopped = True
            print("\nStopping recording...")
            recorder.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Recording to: {session_dir}")
    print("Press Ctrl+C to stop and begin transcription.\n")

    try:
        recorder.start()
    except Exception as e:
        print(f"Error starting recording: {e}", file=sys.stderr)
        sys.exit(1)

    # Block until Ctrl+C
    while not stopped:
        signal.pause()

    # Phase 2: Transcribe
    print("\nTranscribing...")

    mic_result = {"text": "", "segments": [], "words": []}
    sys_result = {"text": "", "segments": [], "words": []}

    mic_path = session_dir / "mic.wav"
    sys_path = session_dir / "system.wav"

    if mic_path.exists():
        mic_result = transcribe(mic_path, config.whisper)
        print(f"  Mic: {len(mic_result['words'])} words")

    if sys_path.exists():
        sys_result = transcribe(sys_path, config.whisper)
        print(f"  System: {len(sys_result['words'])} words")

    timeline = merge_transcripts(mic_result, sys_result)
    duration = max((seg["end"] for seg in timeline), default=0.0)

    display_name = name
    md_path, json_path = write_transcript(
        timeline, mic_result, sys_result,
        session_dir, display_name, duration,
    )
    print(f"  Transcript: {md_path}")

    # Phase 3: Analyze
    if config.env.anthropic_api_key:
        print("\nAnalyzing...")
        output_path = analyze_transcript(session_dir, config)
        print(f"  Analysis: {output_path}")
    else:
        print("\nSkipping analysis (no ANTHROPIC_API_KEY set)")

    print(f"\nDone! Session: {session_dir}")


def cmd_watch(args: argparse.Namespace) -> None:
    """Watch for Zoom/Teams calls and auto-record."""
    from scribe.watcher import watch

    config = load_config()

    print("Scribe watcher started.")
    print("Will auto-record when Zoom or Teams calls are detected.")
    print("Press Ctrl+C to stop.\n")

    try:
        watch(
            config,
            transcribe_after=not args.no_transcribe,
            analyze_after=not args.no_analyze,
        )
    except KeyboardInterrupt:
        print("\nWatcher stopped.")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Start the dashboard web server."""
    import uvicorn
    from scribe.dashboard.app import create_app

    config = load_config()
    app = create_app(config)

    print(f"Starting dashboard at http://{config.dashboard.host}:{config.dashboard.port}")
    uvicorn.run(app, host=config.dashboard.host, port=config.dashboard.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scribe",
        description="Local meeting transcription & analysis",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="command")

    # devices
    sub.add_parser("devices", help="List audio input devices")

    # setup
    sub.add_parser("setup", help="Print BlackHole setup instructions")

    # record
    p_rec = sub.add_parser("record", help="Start recording")
    p_rec.add_argument("--name", "-n", default=None, help="Session name")

    # transcribe
    p_trans = sub.add_parser("transcribe", help="Transcribe a recorded session")
    p_trans.add_argument("session_dir", help="Path to session directory")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Run Claude analysis on transcript")
    p_analyze.add_argument("session_dir", help="Path to session directory")

    # run
    p_run = sub.add_parser("run", help="Record → transcribe → analyze")
    p_run.add_argument("--name", "-n", default=None, help="Session name")

    # watch
    p_watch = sub.add_parser("watch", help="Auto-record Zoom/Teams calls")
    p_watch.add_argument(
        "--no-transcribe", action="store_true",
        help="Skip auto-transcription after recording",
    )
    p_watch.add_argument(
        "--no-analyze", action="store_true",
        help="Skip auto-analysis after transcription",
    )

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Start the dashboard web server")
    p_dash.add_argument("--host", default=None, help="Host to bind to")
    p_dash.add_argument("--port", "-p", type=int, default=None, help="Port to bind to")

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    commands = {
        "devices": cmd_devices,
        "setup": cmd_setup,
        "record": cmd_record,
        "transcribe": cmd_transcribe,
        "analyze": cmd_analyze,
        "run": cmd_run,
        "watch": cmd_watch,
        "dashboard": cmd_dashboard,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
