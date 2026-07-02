"""3-layer context loader (DESIGN §7.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.harness.context.project_layer import ProjectLayer, build_project_layer
from app.harness.context.system_layer import SystemLayer, build_system_layer
from app.harness.context.task_layer import TaskLayer


@dataclass
class ContextPack:
    system: SystemLayer
    project: ProjectLayer
    task: TaskLayer
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        return "\n\n".join([
            self.system.render(),
            self.project.render(),
            self.task.render(),
        ])

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "system": {
                "agent_role": self.system.agent_role,
                "output_schema": self.system.output_schema,
                "hard_constraints": list(self.system.hard_constraints),
            },
            "project": {
                "name": self.project.project,
                "agents_md_chars": len(self.project.agents_md),
                "project_yaml_keys": list(self.project.project_yaml.keys()),
                "repo_link_keys": list(self.project.repo_link.keys()),
                "context_docs": [name for name, _content in self.project.context_docs],
            },
            "task": {
                "user_request_chars": len(self.task.user_request),
                "kb_excerpts": len(self.task.kb_excerpts),
                "upstream_handoff_keys": list(self.task.upstream_handoff.keys()),
            },
            "metadata": dict(self.metadata),
        }


def build_context(
    *,
    agent_role: str,
    output_schema: str,
    project: str,
    user_request: str,
    upstream_handoff: dict[str, str] | None = None,
    kb_excerpts: list[str] | None = None,
) -> ContextPack:
    return ContextPack(
        system=build_system_layer(agent_role=agent_role, output_schema=output_schema),
        project=build_project_layer(project=project),
        task=TaskLayer(
            user_request=user_request,
            kb_excerpts=list(kb_excerpts or []),
            upstream_handoff=dict(upstream_handoff or {}),
        ),
    )
