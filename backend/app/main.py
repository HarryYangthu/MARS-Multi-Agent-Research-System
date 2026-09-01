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
from app.api import agents as agents_api
from app.api import artifacts as artifacts_api
from app.api import chat as chat_api
from app.api import config as config_api
from app.api import context as context_api
from app.api import data_sources as data_sources_api
from app.api import diagnoses as diagnoses_api
from app.api import discovery as discovery_api
from app.api import evaluation as evaluation_api
from app.api import events as events_api
from app.api import execution as execution_api
from app.api import knowledge as knowledge_api
from app.api import projects as projects_api
from app.api import readiness as readiness_api
from app.api import reports as reports_api
from app.api import runtime as runtime_api
from app.api import runs as runs_api
from app.api import stats as stats_api
from app.api import system as system_api
from app.api import templates as templates_api
from app.api import timeline as timeline_api
from app.api import tools as tools_api
from app.api import traces as traces_api
from app.api import websocket as ws_api
from app.api.dependencies import get_event_bus, get_run_store
from app.bridge.agent_registry import get_registry
from app.bridge.candidate_workspace import SecureCandidateWorkspacePreparer
from app.bridge.commander_tools import configure_discovery_commander_tools
from app.bridge.discovery_composition import (
    ProjectPackCandidateAgent,
    ProjectPackRoutingAdapter,
)
from app.bridge.discovery_service import DiscoveryService
from app.bridge.extension_runtime import get_extension_runtime
from app.bridge.idea_selection import IdeaSelectionCoordinator
from app.harness.tools.registry import get_registry as get_tool_registry
from app.settings import get_settings


def register_default_agents() -> None:
    reg = get_registry()
    for cls in (IdeaAgent, ExperimentAgent, CodingAgent, ExecutionAgent, WritingAgent):
        agent = cls()
        if not reg.has(agent.name):
            reg.register(agent.name, agent)


def create_app() -> FastAPI:
    settings = get_settings()
    extension_runtime = get_extension_runtime()

    logger.remove()
    logger.add(sys.stderr, level=settings.mars_log_level)

    app = FastAPI(
        title="MARS",
        description="Multi-Agent Research System",
        version=extension_runtime.profile.core_version,
    )
    app.state.extension_runtime = extension_runtime

    register_default_agents()
    discovery_service = DiscoveryService(
        run_store=get_run_store(),
        event_bus=get_event_bus(),
        candidate_agent=ProjectPackCandidateAgent(extension_runtime),
        adapter=ProjectPackRoutingAdapter(extension_runtime),
        code_candidate_preparer=SecureCandidateWorkspacePreparer(
            tool_registry=get_tool_registry(),
        ),
    )
    discovery_api.configure_discovery_service(discovery_service)
    selection = IdeaSelectionCoordinator(
        run_store=get_run_store(),
        registry=get_registry(),
    )
    discovery_api.configure_idea_selection_handler(selection.apply)
    configure_discovery_commander_tools(discovery_service)
    app.state.discovery_service = discovery_service

    cors_origins = settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials="*" not in cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "mars-backend",
            "version": extension_runtime.profile.version,
            "distribution": extension_runtime.profile.name,
        }

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "MARS V0 backend. See /docs for API spec."}

    app.include_router(runs_api.router)
    app.include_router(context_api.router)
    app.include_router(data_sources_api.router)
    app.include_router(diagnoses_api.router)
    app.include_router(discovery_api.router)
    app.include_router(agents_api.router)
    app.include_router(artifacts_api.router)
    app.include_router(evaluation_api.router)
    app.include_router(timeline_api.router)
    app.include_router(traces_api.router)
    app.include_router(execution_api.router)
    app.include_router(knowledge_api.router)
    app.include_router(templates_api.router)
    app.include_router(tools_api.router)
    app.include_router(tools_api.run_router)
    app.include_router(projects_api.router)
    app.include_router(readiness_api.router)
    app.include_router(runtime_api.router)
    app.include_router(config_api.router)
    app.include_router(reports_api.router)
    app.include_router(events_api.router)
    app.include_router(stats_api.router)
    app.include_router(system_api.router)
    app.include_router(chat_api.router)
    app.include_router(ws_api.router)

    logger.info(
        "MARS backend ready (distribution={}, core={}, port={})",
        extension_runtime.profile.name,
        extension_runtime.profile.core_version,
        settings.backend_port,
    )
    return app


app = create_app()
