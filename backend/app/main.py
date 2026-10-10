"""MARS V0 backend entry."""
from __future__ import annotations

import sys
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from openai import APIError

from app.agents.coding.agent import CodingAgent
from app.agents.execution.agent import ExecutionAgent
from app.agents.experiment.agent import ExperimentAgent
from app.agents.idea.runtime_profile import resolve_idea_profile
from app.agents.idea.service_agent import ServiceIdeaAgent
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.writing.agent import WritingAgent
from app.api import agents as agents_api
from app.api import ainative as ainative_api
from app.api import artifacts as artifacts_api
from app.api import chat as chat_api
from app.api import capabilities as capabilities_api
from app.api import config as config_api
from app.api import context as context_api
from app.api import data_sources as data_sources_api
from app.api import data_pipeline as data_pipeline_api
from app.api import diagnoses as diagnoses_api
from app.api import discovery as discovery_api
from app.api import evaluation as evaluation_api
from app.api import events as events_api
from app.api import execution_config as execution_config_api
from app.api import experiments as experiments_api
from app.api import execution as execution_api
from app.api import tensorboard as tensorboard_api
from app.bridge.tensorboard_service import shutdown_tensorboard
from app.api import knowledge as knowledge_api
from app.api import projects as projects_api
from app.api import readiness as readiness_api
from app.api import reports as reports_api
from app.api import results as results_api
from app.api import runtime as runtime_api
from app.api import runs as runs_api
from app.api import research_contracts as research_contracts_api
from app.api import research_templates as research_templates_api
from app.api import stats as stats_api
from app.api import system as system_api
from app.api import templates as templates_api
from app.api import timeline as timeline_api
from app.api import tools as tools_api
from app.api import traces as traces_api
from app.api import websocket as ws_api
from app.api.dependencies import get_event_bus, get_run_store, shutdown_owned_runs
from app.api.llm_errors import llm_error_response
from app.api.desktop_session import DesktopSessionMiddleware
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
from app.harness.runtime.readiness import check_git_readiness
from app.settings import get_settings


def register_default_agents() -> None:
    reg = get_registry()
    selector = get_settings().mars_idea_runtime_profile
    idea = (FocusedIdeaAgent() if selector == "focused_v1"
            else ServiceIdeaAgent(profile=resolve_idea_profile(selector)))
    if not reg.has(idea.name):
        reg.register(idea.name, idea)
    else:
        existing = reg.get(idea.name)
        if isinstance(existing, (ServiceIdeaAgent, FocusedIdeaAgent)):
            if existing.service_profile_snapshot != idea.service_profile_snapshot:
                raise ValueError("Idea runtime profile changed after registration; restart the service")
        elif idea.service_profile_snapshot is not None:
            raise ValueError("experimental Idea profile requires its service-start registered agent")
    for cls in (ExperimentAgent, CodingAgent, ExecutionAgent, WritingAgent):
        agent = cls()
        if not reg.has(agent.name):
            reg.register(agent.name, agent)


@asynccontextmanager
async def service_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    git_check = await asyncio.to_thread(check_git_readiness)
    _app.state.git_readiness = git_check
    if not git_check.ready:
        logger.warning("Git readiness: {}", git_check.message)
    try:
        yield
    finally:
        await shutdown_owned_runs()
        await shutdown_tensorboard()


def create_app() -> FastAPI:
    settings = get_settings()
    extension_runtime = get_extension_runtime()

    logger.remove()
    logger.add(sys.stderr, level=settings.mars_log_level)

    app = FastAPI(
        title="MARS",
        description="Multi-Agent Research System",
        version=extension_runtime.profile.core_version,
        lifespan=service_lifespan,
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
    app.add_exception_handler(APIError, llm_error_response)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials="*" not in cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    desktop_token = settings.mars_desktop_session_token.get_secret_value()
    if desktop_token:
        app.add_middleware(
            DesktopSessionMiddleware, token=desktop_token, origins=tuple(cors_origins)
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
    app.include_router(research_contracts_api.router)
    app.include_router(research_templates_api.router)
    app.include_router(capabilities_api.router)
    app.include_router(context_api.router)
    app.include_router(data_sources_api.router)
    app.include_router(data_pipeline_api.router)
    app.include_router(diagnoses_api.router)
    app.include_router(discovery_api.router)
    app.include_router(agents_api.router)
    app.include_router(artifacts_api.router)
    app.include_router(evaluation_api.router)
    app.include_router(timeline_api.router)
    app.include_router(traces_api.router)
    app.include_router(execution_api.router)
    app.include_router(tensorboard_api.router)
    app.include_router(knowledge_api.router)
    app.include_router(templates_api.router)
    app.include_router(tools_api.router)
    app.include_router(tools_api.run_router)
    app.include_router(projects_api.router)
    app.include_router(experiments_api.router)
    app.include_router(ainative_api.router)
    app.include_router(execution_config_api.router)
    app.include_router(readiness_api.router)
    app.include_router(runtime_api.router)
    app.include_router(config_api.router)
    app.include_router(reports_api.router)
    app.include_router(results_api.router)
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
