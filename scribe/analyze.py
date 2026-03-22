"""Claude API post-meeting analysis."""

from __future__ import annotations

import logging
from pathlib import Path

import anthropic

from scribe.config import AppConfig

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
You are analyzing a meeting transcript. The transcript has two speakers:
- **You**: The user who recorded the meeting
- **Remote**: The remote participant(s)

Provide a structured analysis with these sections:

## Summary
A concise 2-3 sentence summary of the meeting.

## Key Discussion Points
Bullet points of the main topics discussed.

## Action Items
Bullet points of any commitments, tasks, or follow-ups mentioned.
Indicate who is responsible (You / Remote / Both) for each.

## Decisions Made
Any explicit decisions or agreements reached.

## Speaking Patterns
Brief observations about communication dynamics:
- Approximate speaking ratio
- Who drove the conversation
- Any notable patterns (interruptions, long pauses, topic changes)

## Notes for Follow-up
Anything that seemed unresolved or worth revisiting.
"""


def analyze_transcript(session_dir: Path, config: AppConfig) -> Path:
    """Run Claude analysis on a transcript and write analysis.md."""
    transcript_path = session_dir / "transcript.md"
    if not transcript_path.exists():
        raise FileNotFoundError(f"No transcript found at {transcript_path}")

    transcript = transcript_path.read_text()

    api_key = config.env.anthropic_api_key
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Add it to .env or export it."
        )

    client = anthropic.Anthropic(api_key=api_key)

    logger.info(f"Analyzing transcript with {config.analysis.model}...")

    message = client.messages.create(
        model=config.analysis.model,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{ANALYSIS_PROMPT}\n\n"
                    f"---\n\n"
                    f"Here is the transcript:\n\n{transcript}"
                ),
            }
        ],
    )

    analysis_text = message.content[0].text

    # Write with header
    session_name = session_dir.name
    output = f"# Analysis: {session_name}\n\n{analysis_text}\n"

    output_path = session_dir / "analysis.md"
    output_path.write_text(output)

    logger.info(f"Analysis written to {output_path}")
    return output_path
