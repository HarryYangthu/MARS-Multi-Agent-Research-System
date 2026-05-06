"""MARS V0 backend entry."""
from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.agents.coding.agent import CodingAgent
from app.agents.execution.agent import ExecutionAgent
from app.agents.experiment.agent import ExperimentAgent
from app.agents.idea.agent import IdeaAgent
from app.agents.writing.agent import WritingAgent
from app.api import artifacts as artifacts_api
from app.api import events as events_api
from app.api import execution as execution_api
from app.api import knowledge as knowledge_api
from app.api import projects as projects_api
from app.api import runs as runs_api
from app.api import stats as stats_api
from app.api import templates as templates_api
from app.api import websocket as ws_api
from app.bridge.agent_registry import get_registry
from app.settings import get_settings


def register_default_agents() -> None:
    reg = get_registry()
    for cls in (IdeaAgent, ExperimentAgent, CodingAgent, ExecutionAgent, WritingAgent):
        agent = cls()
        if not reg.has(agent.name):
            reg.register(agent.name, agent)


def create_app() -> FastAPI:
    settings = get_settings()

    logger.remove()
    logger.add(sys.stderr, level=settings.mars_log_level)

    app = FastAPI(
        title="MARS V0",
        description="Multi-Agent Research System — V0 backend",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "mars-backend", "version": "0.1.0"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "MARS V0 backend. See /docs for API spec."}

    app.include_router(runs_api.router)
    app.include_router(artifacts_api.router)
    app.include_router(execution_api.router)
    app.include_router(knowledge_api.router)
    app.include_router(templates_api.router)
    app.include_router(projects_api.router)
    app.include_router(events_api.router)
    app.include_router(stats_api.router)
    app.include_router(ws_api.router)

    register_default_agents()

    logger.info("MARS V0 backend ready (port={})", settings.backend_port)
    return app


app = create_app()
