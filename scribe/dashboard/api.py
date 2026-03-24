"""HTMX fragment + JSON API endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from scribe.dashboard.sessions import ActionItem, CustomAnalysis, SessionIndex
from scribe.utils import format_duration

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_index(request: Request) -> SessionIndex:
    return request.app.state.index


def _fragment(request: Request, template: str, **ctx) -> HTMLResponse:
    templates = request.app.state.templates
    ctx["request"] = request
    ctx["format_duration"] = format_duration
    return templates.TemplateResponse(request, template, ctx)


# --- Session metadata ---

@router.patch("/sessions/{dir_name}")
async def update_session(request: Request, dir_name: str):
    index = _get_index(request)
    data = await request.json()
    session = index.update_session_meta(dir_name, **data)
    if not session:
        return HTMLResponse("not found", status_code=404)
    return {"ok": True}


# --- Action items ---

@router.get("/fragments/action-items/{dir_name}")
async def action_items_fragment(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)
    return _fragment(request, "fragments/action_items.html", session=session)


@router.patch("/sessions/{dir_name}/actions/{action_id}")
async def toggle_action(request: Request, dir_name: str, action_id: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)

    for item in session.meta.action_items:
        if item.id == action_id:
            if item.status == "open":
                item.status = "done"
                item.completed_at = datetime.now().isoformat()
            else:
                item.status = "open"
                item.completed_at = None
            break

    index.update_session_meta(dir_name, action_items=session.meta.action_items)

    # Find the updated item
    action = next((a for a in session.meta.action_items if a.id == action_id), None)
    if not action:
        return HTMLResponse("not found", status_code=404)
    return _fragment(request, "fragments/action_item_row.html", session=session, action=action)


@router.post("/sessions/{dir_name}/actions")
async def add_action(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)

    data = await request.json()
    item = ActionItem(
        id=f"ai_{uuid.uuid4().hex[:8]}",
        text=data.get("text", ""),
        owner=data.get("owner", "You"),
        status="open",
        created_at=datetime.now().isoformat(),
    )
    session.meta.action_items.append(item)
    index.update_session_meta(dir_name, action_items=session.meta.action_items)

    return _fragment(request, "fragments/action_items.html", session=session)


@router.delete("/sessions/{dir_name}/actions/{action_id}")
async def delete_action(request: Request, dir_name: str, action_id: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)

    session.meta.action_items = [a for a in session.meta.action_items if a.id != action_id]
    index.update_session_meta(dir_name, action_items=session.meta.action_items)

    return _fragment(request, "fragments/action_items.html", session=session)


# --- Tag editor ---

@router.get("/fragments/tag-editor/{dir_name}")
async def tag_editor_fragment(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)
    return _fragment(request, "fragments/tag_editor.html", session=session, editing=True)


@router.get("/fragments/tag-display/{dir_name}")
async def tag_display_fragment(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)
    return _fragment(request, "fragments/tag_editor.html", session=session, editing=False)


# --- Analysis ---

@router.post("/sessions/{dir_name}/reanalyze")
async def reanalyze(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)

    config = request.app.state.config
    session_dir = Path(session.dir_path)

    from scribe.analyze import analyze_transcript
    analyze_transcript(session_dir, config)

    # Reload session data
    session = index.reload_session(dir_name)
    analysis_html = markdown.markdown(session.analysis_md) if session and session.analysis_md else ""

    return _fragment(request, "fragments/analysis_section.html", session=session, analysis_html=analysis_html)


@router.post("/sessions/{dir_name}/custom-analyze")
async def custom_analyze(request: Request, dir_name: str):
    index = _get_index(request)
    session = index.get_session(dir_name)
    if not session:
        return HTMLResponse("not found", status_code=404)

    data = await request.json()
    prompt = data.get("prompt", "")
    if not prompt or not session.transcript_md:
        return HTMLResponse("missing prompt or transcript", status_code=400)

    config = request.app.state.config
    import anthropic
    client = anthropic.Anthropic(api_key=config.env.anthropic_api_key)

    message = client.messages.create(
        model=config.analysis.model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"{prompt}\n\n---\n\nHere is the transcript:\n\n{session.transcript_md}",
        }],
    )

    result_text = message.content[0].text
    ca = CustomAnalysis(
        id=f"ca_{uuid.uuid4().hex[:8]}",
        prompt=prompt,
        result=result_text,
        created_at=datetime.now().isoformat(),
    )
    session.meta.custom_analyses.append(ca)
    index.update_session_meta(dir_name, custom_analyses=session.meta.custom_analyses)

    result_html = markdown.markdown(result_text)
    return _fragment(request, "fragments/custom_analysis.html",
                     ca={"id": ca.id, "prompt": ca.prompt, "result_html": result_html, "created_at": ca.created_at})


# --- Search / filter fragments ---

@router.get("/fragments/session-list")
async def session_list_fragment(request: Request):
    index = _get_index(request)
    q = request.query_params.get("q")
    tag = request.query_params.get("tag")
    meeting_type = request.query_params.get("type")

    sessions = index.list_sessions(q=q, tag=tag, meeting_type=meeting_type)
    return _fragment(request, "fragments/session_card.html", sessions=sessions)


@router.get("/fragments/stats")
async def stats_fragment(request: Request):
    index = _get_index(request)
    index.refresh()
    stats = index.aggregate_stats()
    return _fragment(request, "fragments/stats_cards.html", stats=stats)
