"""FastAPI app factory for the Scribe dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scribe.config import AppConfig
from scribe.dashboard.sessions import SessionIndex

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(config: AppConfig) -> FastAPI:
    """Create and configure the dashboard FastAPI app."""

    index = SessionIndex(config.output.base_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        index.refresh()
        logger.info(f"Dashboard ready — {len(index.sessions)} sessions loaded")
        yield
        # Shutdown
        logger.info("Dashboard shutting down")

    app = FastAPI(title="Scribe", lifespan=lifespan)

    # Store in app state for access from routes
    app.state.config = config
    app.state.index = index
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Mount recordings for audio file serving
    if config.output.base_path.exists():
        app.mount(
            "/audio",
            StaticFiles(directory=str(config.output.base_path)),
            name="audio",
        )

    # Register routers
    from scribe.dashboard.routes import router as page_router
    from scribe.dashboard.api import router as api_router

    app.include_router(page_router)
    app.include_router(api_router, prefix="/api")

    return app
