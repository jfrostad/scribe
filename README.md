# Scribe

Local meeting transcription & analysis. Records two audio streams (your mic + system audio via BlackHole), transcribes with faster-whisper, and optionally runs Claude analysis for summaries and action items.

Everything runs locally — no audio leaves your machine.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Check your audio devices
scribe devices

# Record a meeting (Ctrl+C to stop)
scribe record --name standup

# Transcribe it
scribe transcribe recordings/2026-03-22_standup

# Or do everything in one shot
scribe run --name standup
```

## How it works

```
Mic (Brio 101)        → mic.wav    → whisper → "You" segments     ┐
                                                                    ├→ merged transcript.md → Claude analysis
System (BlackHole 2ch) → system.wav → whisper → "Remote" segments  ┘
```

Two streams = free speaker diarization. No ML diarization models needed.

## Setup

1. `brew install --cask blackhole-2ch` + reboot
2. Create a Multi-Output Device in Audio MIDI Setup (speakers + BlackHole)
3. Set it as system output
4. Add `ANTHROPIC_API_KEY=sk-ant-...` to `.env` (for `analyze` command)

Run `scribe setup` for detailed instructions.

## Commands

| Command | Description |
|---------|-------------|
| `scribe devices` | List audio input devices |
| `scribe record --name NAME` | Record mic + system audio |
| `scribe transcribe SESSION_DIR` | Transcribe → markdown + JSON |
| `scribe analyze SESSION_DIR` | Claude analysis → action items, summary |
| `scribe run --name NAME` | Record → transcribe → analyze |
| `scribe watch` | Auto-record Zoom/Teams calls (daemon) |
| `scribe setup` | BlackHole setup instructions |

## Auto-recording

`scribe watch` runs as a background daemon that detects active Zoom/Teams calls and automatically records, transcribes, and analyzes them.

```bash
# Run manually
scribe watch

# Or install as a login service (auto-starts on boot)
ln -s ~/Documents/work/tools/scribe/com.jfrostad.scribe-watch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jfrostad.scribe-watch.plist
```

Detection: polls every 5s for Zoom's `CptHost` process or Teams SlimCore connected UDP media streams. Logs to `recordings/scribe-watch.log`.

## Docs

See `docs/index.html` for full technical documentation.
