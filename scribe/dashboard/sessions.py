"""Session data loading, indexing, and search."""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ActionItem(BaseModel):
    id: str = ""
    text: str = ""
    owner: str = ""  # You, Remote, Both
    status: str = "open"  # open, done
    created_at: str = ""
    completed_at: str | None = None


class CustomAnalysis(BaseModel):
    id: str = ""
    prompt: str = ""
    result: str = ""
    created_at: str = ""


class SessionMeta(BaseModel):
    """Mutable user metadata — stored in session.meta.json."""

    tags: list[str] = Field(default_factory=list)
    meeting_type: str = ""
    project: str = ""
    starred: bool = False
    action_items: list[ActionItem] = Field(default_factory=list)
    custom_analyses: list[CustomAnalysis] = Field(default_factory=list)
    notes: str = ""


class SessionData(BaseModel):
    """Combined session data from both JSON sidecars."""

    dir_name: str = ""
    dir_path: str = ""
    session_name: str = ""
    date: str = ""
    duration_secs: float = 0.0
    timeline: list[dict] = Field(default_factory=list)
    word_count: int = 0
    you_duration: float = 0.0
    remote_duration: float = 0.0
    you_words: int = 0
    remote_words: int = 0
    analysis_md: str | None = None
    analysis_summary: str | None = None
    transcript_md: str | None = None
    # Mutable metadata
    meta: SessionMeta = Field(default_factory=SessionMeta)


def _parse_action_items_from_analysis(analysis_md: str) -> list[ActionItem]:
    """Extract action items from the analysis markdown."""
    items = []
    in_action_section = False
    now = datetime.now().isoformat()

    for line in analysis_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Action Items") or stripped.startswith("## Action items"):
            in_action_section = True
            continue
        if stripped.startswith("## ") and in_action_section:
            break
        if in_action_section and (stripped.startswith("- ") or stripped.startswith("• ") or stripped.startswith("* ")):
            text = stripped[2:].strip()
            # Try to extract owner from parenthetical like (You) or (Remote)
            owner = ""
            owner_match = re.search(r"\((?:Owner:\s*)?(You|Remote|Both)\)", text, re.IGNORECASE)
            if owner_match:
                owner = owner_match.group(1).capitalize()
                text = text[:owner_match.start()].rstrip(" -–—,") + text[owner_match.end():]
                text = text.strip()
            items.append(ActionItem(
                id=f"ai_{uuid.uuid4().hex[:8]}",
                text=text,
                owner=owner,
                status="open",
                created_at=now,
            ))

    return items


def _compute_speaker_stats(timeline: list[dict]) -> dict:
    """Compute speaking duration and word counts per speaker."""
    you_dur = 0.0
    remote_dur = 0.0
    you_words = 0
    remote_words = 0

    for seg in timeline:
        dur = seg.get("end", 0) - seg.get("start", 0)
        wc = len(seg.get("text", "").split())
        if seg.get("speaker") == "You":
            you_dur += dur
            you_words += wc
        else:
            remote_dur += dur
            remote_words += wc

    return {
        "you_duration": you_dur,
        "remote_duration": remote_dur,
        "you_words": you_words,
        "remote_words": remote_words,
        "word_count": you_words + remote_words,
    }


def _extract_summary(analysis_md: str) -> str | None:
    """Extract the first paragraph after ## Summary."""
    in_summary = False
    lines = []
    for line in analysis_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Summary"):
            in_summary = True
            continue
        if stripped.startswith("## ") and in_summary:
            break
        if in_summary and stripped:
            lines.append(stripped)

    return " ".join(lines)[:300] if lines else None


class SessionIndex:
    """In-memory index of all recording sessions."""

    def __init__(self, recordings_dir: Path):
        self.recordings_dir = recordings_dir
        self.sessions: dict[str, SessionData] = {}

    def refresh(self) -> None:
        """Scan recordings directory and rebuild index."""
        self.sessions.clear()
        if not self.recordings_dir.exists():
            return

        for d in sorted(self.recordings_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            transcript_meta = d / "transcript.meta.json"
            if not transcript_meta.exists():
                continue

            try:
                session = self._load_session(d)
                self.sessions[d.name] = session
            except Exception as e:
                logger.warning(f"Failed to load session {d.name}: {e}")

        logger.info(f"Loaded {len(self.sessions)} sessions")

    def _load_session(self, session_dir: Path) -> SessionData:
        """Load a single session from disk."""
        # Transcript metadata (immutable)
        with open(session_dir / "transcript.meta.json") as f:
            transcript_data = json.load(f)

        timeline = transcript_data.get("timeline", [])
        stats = _compute_speaker_stats(timeline)

        # Analysis markdown
        analysis_md = None
        analysis_summary = None
        analysis_path = session_dir / "analysis.md"
        if analysis_path.exists():
            analysis_md = analysis_path.read_text()
            analysis_summary = _extract_summary(analysis_md)

        # Transcript markdown
        transcript_md = None
        transcript_path = session_dir / "transcript.md"
        if transcript_path.exists():
            transcript_md = transcript_path.read_text()

        # User metadata (mutable)
        meta = SessionMeta()
        meta_path = session_dir / "session.meta.json"
        if meta_path.exists():
            with open(meta_path) as f:
                meta = SessionMeta(**json.load(f))

        # Seed action items from analysis if none exist yet
        if not meta.action_items and analysis_md:
            meta.action_items = _parse_action_items_from_analysis(analysis_md)

        return SessionData(
            dir_name=session_dir.name,
            dir_path=str(session_dir),
            session_name=transcript_data.get("session_name", session_dir.name),
            date=transcript_data.get("date", ""),
            duration_secs=transcript_data.get("duration_secs", 0.0),
            timeline=timeline,
            analysis_md=analysis_md,
            analysis_summary=analysis_summary,
            transcript_md=transcript_md,
            meta=meta,
            **stats,
        )

    def list_sessions(
        self,
        q: str | None = None,
        tag: str | None = None,
        meeting_type: str | None = None,
        starred: bool | None = None,
        limit: int = 50,
    ) -> list[SessionData]:
        """Filtered, sorted session list."""
        results = list(self.sessions.values())

        if q:
            q_lower = q.lower()
            results = [
                s for s in results
                if q_lower in s.session_name.lower()
                or q_lower in (s.transcript_md or "").lower()
                or q_lower in (s.analysis_md or "").lower()
                or any(q_lower in t.lower() for t in s.meta.tags)
            ]

        if tag:
            results = [s for s in results if tag in s.meta.tags]

        if meeting_type:
            results = [s for s in results if s.meta.meeting_type == meeting_type]

        if starred is not None:
            results = [s for s in results if s.meta.starred == starred]

        # Sort by date descending
        results.sort(key=lambda s: s.date, reverse=True)
        return results[:limit]

    def get_session(self, dir_name: str) -> SessionData | None:
        """Single session with full data."""
        return self.sessions.get(dir_name)

    def update_session_meta(self, dir_name: str, **kwargs: Any) -> SessionData | None:
        """Write updates to session.meta.json."""
        session = self.sessions.get(dir_name)
        if not session:
            return None

        # Update fields
        for key, value in kwargs.items():
            if hasattr(session.meta, key):
                setattr(session.meta, key, value)

        # Write to disk
        meta_path = Path(session.dir_path) / "session.meta.json"
        meta_path.write_text(json.dumps(session.meta.model_dump(), indent=2))

        return session

    def reload_session(self, dir_name: str) -> SessionData | None:
        """Reload a single session from disk."""
        session_dir = self.recordings_dir / dir_name
        if not session_dir.exists():
            return None
        try:
            session = self._load_session(session_dir)
            self.sessions[dir_name] = session
            return session
        except Exception as e:
            logger.warning(f"Failed to reload session {dir_name}: {e}")
            return None

    def aggregate_stats(self) -> dict:
        """Cross-session analytics data."""
        sessions = list(self.sessions.values())
        total_duration = sum(s.duration_secs for s in sessions)
        total_you = sum(s.you_duration for s in sessions)
        total_remote = sum(s.remote_duration for s in sessions)
        open_actions = sum(
            1 for s in sessions
            for a in s.meta.action_items
            if a.status == "open"
        )
        done_actions = sum(
            1 for s in sessions
            for a in s.meta.action_items
            if a.status == "done"
        )

        # Tag counts
        tag_counts: dict[str, int] = {}
        for s in sessions:
            for t in s.meta.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        # Meeting type counts
        type_counts: dict[str, int] = {}
        for s in sessions:
            mt = s.meta.meeting_type or "uncategorized"
            type_counts[mt] = type_counts.get(mt, 0) + 1

        return {
            "total_sessions": len(sessions),
            "total_duration_secs": total_duration,
            "total_duration_hours": total_duration / 3600,
            "total_you_duration": total_you,
            "total_remote_duration": total_remote,
            "open_actions": open_actions,
            "done_actions": done_actions,
            "total_actions": open_actions + done_actions,
            "tag_counts": sorted(tag_counts.items(), key=lambda x: -x[1]),
            "type_counts": sorted(type_counts.items(), key=lambda x: -x[1]),
        }

    def delete_session(self, dir_name: str) -> bool:
        """Permanently delete a session directory."""
        session = self.sessions.get(dir_name)
        if not session:
            return False
        session_dir = Path(session.dir_path)
        if session_dir.exists():
            shutil.rmtree(session_dir)
        del self.sessions[dir_name]
        logger.info(f"Deleted session {dir_name}")
        return True

    def archive_session(self, dir_name: str) -> bool:
        """Move a session to recordings/archive/."""
        session = self.sessions.get(dir_name)
        if not session:
            return False
        session_dir = Path(session.dir_path)
        archive_dir = self.recordings_dir / "archive" / dir_name
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(session_dir), str(archive_dir))
        del self.sessions[dir_name]
        logger.info(f"Archived session {dir_name} → {archive_dir}")
        return True

    def unarchive_session(self, dir_name: str) -> bool:
        """Move a session back from archive."""
        archive_dir = self.recordings_dir / "archive" / dir_name
        if not archive_dir.exists():
            return False
        target_dir = self.recordings_dir / dir_name
        shutil.move(str(archive_dir), str(target_dir))
        # Reload into index
        self.reload_session(dir_name)
        logger.info(f"Unarchived session {dir_name}")
        return True

    def list_archived(self) -> list[str]:
        """List archived session directory names."""
        archive_dir = self.recordings_dir / "archive"
        if not archive_dir.exists():
            return []
        return sorted(
            [d.name for d in archive_dir.iterdir() if d.is_dir()],
            reverse=True,
        )

    def all_tags(self) -> list[str]:
        """All unique tags across sessions."""
        tags = set()
        for s in self.sessions.values():
            tags.update(s.meta.tags)
        return sorted(tags)
