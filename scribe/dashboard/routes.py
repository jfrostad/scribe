"""HTML page routes for the Scribe dashboard."""

from __future__ import annotations

import markdown
from fastapi import APIRouter, Request

from scribe.dashboard.sessions import SessionIndex
from scribe.utils import format_duration

router = APIRouter()


def _get_index(request: Request) -> SessionIndex:
    return request.app.state.index


def _render(request: Request, template: str, **ctx):
    templates = request.app.state.templates
    ctx["request"] = request
    ctx["format_duration"] = format_duration
    return templates.TemplateResponse(request, template, ctx)


@router.get("/")
async def home(request: Request):
    index = _get_index(request)
    stats = index.aggregate_stats()
    recent = index.list_sessions(limit=5)

    # Open action items across all sessions
    open_actions = []
    for s in index.list_sessions(limit=50):
        for a in s.meta.action_items:
            if a.status == "open":
                open_actions.append({"session": s, "action": a})
    open_actions = open_actions[:5]

    return _render(
        request, "home.html",
        page="home",
        stats=stats,
        recent=recent,
        open_actions=open_actions,
    )


@router.get("/sessions")
async def sessions_list(request: Request):
    index = _get_index(request)
    q = request.query_params.get("q")
    tag = request.query_params.get("tag")
    meeting_type = request.query_params.get("type")

    sessions = index.list_sessions(q=q, tag=tag, meeting_type=meeting_type)
    all_tags = index.all_tags()

    return _render(
        request, "sessions.html",
        page="sessions",
        sessions=sessions,
        all_tags=all_tags,
        filter_q=q or "",
        filter_tag=tag or "",
        filter_type=meeting_type or "",
    )


@router.get("/sessions/{dir_name}")
async def session_detail(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return _render(request, "home.html", page="home", stats=index.aggregate_stats(), recent=[], open_actions=[])

    # Render analysis markdown to HTML
    analysis_html = ""
    if session.analysis_md:
        analysis_html = markdown.markdown(session.analysis_md)

    # Render custom analyses
    custom_analyses_html = []
    for ca in session.meta.custom_analyses:
        custom_analyses_html.append({
            "id": ca.id,
            "prompt": ca.prompt,
            "result_html": markdown.markdown(ca.result),
            "created_at": ca.created_at,
        })

    # Speaking stats
    total_speaking = session.you_duration + session.remote_duration
    you_pct = (session.you_duration / total_speaking * 100) if total_speaking > 0 else 0
    remote_pct = 100 - you_pct

    return _render(
        request, "session_detail.html",
        page="sessions",
        session=session,
        analysis_html=analysis_html,
        custom_analyses_html=custom_analyses_html,
        you_pct=you_pct,
        remote_pct=remote_pct,
    )


@router.get("/analytics")
async def analytics(request: Request):
    index = _get_index(request)
    stats = index.aggregate_stats()
    sessions = index.list_sessions(limit=30)

    # Per-session speaking data for trend chart
    speaking_data = []
    for s in reversed(sessions[:20]):
        total = s.you_duration + s.remote_duration
        speaking_data.append({
            "name": s.session_name,
            "date": s.date[:10] if s.date else "",
            "you_pct": (s.you_duration / total * 100) if total > 0 else 0,
            "remote_pct": (s.remote_duration / total * 100) if total > 0 else 0,
            "duration_secs": s.duration_secs,
        })

    # Meeting frequency by date
    date_counts: dict[str, int] = {}
    for s in sessions:
        d = s.date[:10] if s.date else ""
        if d:
            date_counts[d] = date_counts.get(d, 0) + 1

    return _render(
        request, "analytics.html",
        page="analytics",
        stats=stats,
        speaking_data=speaking_data,
        date_counts=date_counts,
        sessions=sessions,
    )
