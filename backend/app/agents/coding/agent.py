"""Coding Agent — experiment_plan → code_spec + patch."""
from __future__ import annotations

import socket
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.coding.opencode_adapter import OpenCodeAdapter
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.llm.openai_provider import CustomEndpointProvider, LocalVllmProvider
from app.harness.llm.post_training_loader import PostTrainingHandle, load_handle
from app.harness.llm.provider_base import LLMConfig, LLMProvider
from app.settings import env_or_local, get_settings, repo_root


class CodingAgent(BaseAgent):
    name = "coding"
    project_knowledge_enabled = True
    output_schema = "code_spec.v1"
    agent_brief = (
        "你负责把实验方案转化为代码规格 (code_spec) 与补丁。先用 code.repo_reader 阅读"
        "待改文件、knowledge.code_assets 复用既有实现,严格保持 baseline 接口兼容"
        "(baseline_compat.preserved=true,违反会触发 Gate 5)。产物列出 files_changed、"
        "new_dependencies 与真实测试覆盖。使用 code.patch_generator 生成补丁并通过 code.apply_patch "
        "实际落地，或通过 code.write_file 完成允许的改动；所有写入仍必须经过工具权限与 Gate 5。"
        "完成前核对工具执行成功，正文保留实际 diff；只提出补丁而未写入不算实现完成。"
        "无真实测试结果时如实标注 skipped，不声称测试通过。"
        "已有结构、入口和训练方式均不变的明确YAML参数任务，按配置修改交付：先读真实源配置与加载器继承，"
        "优先 code.copy_yaml 单次批量复制，只改用户指定参数；不要为配置复制新增测试文件、重写训练脚本或运行全仓测试。"
        "工具参数为 source、variants=[{path,updates}] 与可选 common_updates；updates使用嵌套mapping，叶值为JSON标量，不能写点路径。"
        "公共预算/seed等放common_updates，各组差异放updates。确需新增已明确字段时才用allow_new_keys=true，不猜默认。"
        "源配置依赖同目录base等文件时先核对加载器，另一次copy_yaml显式复制依赖，不能假设跨目录自动继承。"
        "不要为清理旧配置而删除或迁移文件；保留已有写入，按真实运行清单引用需要的配置。"
        "派生配置交由MARS逐组实际入口的不训练预检核验；它检查配置继承、必需字段和模型构建，不能用整仓单测替代。"
        "确有源码/接口修改或真实预检失败时，只补受影响的已有检查与已定位问题，禁止为看起来完整而制造测试和重复检查。"
        "测试只能核对纯函数、schema、真实文件和真实进程；禁止用替身训练器、Mock、monkeypatch "
        "或固定指标伪造模型/工具/执行成功。新增作业适配器时先读取 configured_execution_delivery "
        "中的真实请求/结果协议，按相同执行配置检查解析和结果格式；不要混淆 MARS 包装入口与训练入口。"
        "交付 execution_jobs：名称逐项对应已批准实验，每项 config 绑定实际启动入口 entrypoint、"
        "配置文件 config_path 或 MARS command_id；明确 seed、预算数值和单位，不能替换批准参数。"
        "明确的基线与候选对照，请在各项 config.role 保留或填写已确认的 baseline/candidate 身份标签，"
        "供结果展示对齐；保留批准清单的既有 role，未确认的分组不猜测，不借标签改变科学配置、数据、种子或预算。"
        "按用户指定的 entrypoint 与 config_path 精确选择 MARS 执行能力；没有匹配能力时明确报告缺失。"
        "不能为迁就登记命令扩大用户指定的模型集合、替换配置、改变数据划分或指标口径。"
        "有批准实验方案时 experiment_plan_sha256 必须绑定该完整文档；没有方案时按用户任务交付运行清单，不得捏造方案哈希。"
        "交付前逐组核对实际配置文件的种子与训练单位。当前入口不能满足批准预算时实现正确入口，"
        "不能把 steps 解释成 epochs；未落实交接约束不能提交成功。"
        "步骤预算在交付中使用 budget_steps，或 budget_unit=steps + max_iters；"
        "同一数值的两种字段是预算别名，不能改写批准数值或改成 epochs。"
        "重试时先检查当前分支已有改动，保留正确实现，只修复已定位问题。"
        "MARS 为本任务固定一个 Git 分支，并在编码完成、停止或失败时自动提交真实写入改动。"
        "不要自行创建或切换分支，不要执行 stash/reset/pull，也不要重复提交；自动提交不代表方案已批准。"
        "files_changed 覆盖本任务整条成功写入链形成的净改动；前轮写入且保留的净改动仍须列出。"
        "相同内容重写是复用，不能声明新增；只有整条写入链没有净差异时 files_changed=[]。"
        "正文列出复用文件、指纹及真实检查；不要为了登记制造改动或重复应用补丁。"
        "同范围检查已有成功真实收据，且相关源码、配置、检查命令及执行环境输入均未变时，引用并复用收据，"
        "不要重复运行整套检查；输入变化、先前失败或证据无法确认时，按受影响范围重新实际检查。"
        "不得伪造检查通过，也不能以复用为由省略变化后的必要检查或本次必须的运行预检。"
        "正文中的示意 diff 不会作为补丁执行；完整补丁须有文件头与正确行号，禁止省略号。"
        "不要为了消除交付字段冲突删除基线配置中无关的 Epoch/Etotal/epoch 控制字段。"
        "已有模型的有限改参任务，优先复用 configured_execution_delivery 中登记命令 label 明确声明的"
        "现有适配器观测能力；训练脚本未直接写 TensorBoard 不代表执行器缺少观测。"
        "适配器已负责观测时，保留其回调和日志文件约定，不重复注入 Observer 或再次写入同一步骤日志。"
        "没有能力声明或真实证据时不得声称已支持；以真实预检与运行日志核对缺口，不能把声明当作运行成功。"
        "仿真入口默认启用 TensorBoard，消费 MARS MARS_TENSORBOARD_ENABLED=1；"
        "按真实更新记录损失、学习率、验证指标和参数快照，并将当前代码仓本次生成的"
        "频谱、通道对比、损失图、系数热图及非线性特性全部保存在当前作业目录。"
        "验证日志实际写出，只有最终结果单点不算完整过程；未采集内容明确说明，禁止补造。"
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Eagerly resolve post_training so configuration errors surface early.
        pt_raw = self._config.raw.get("post_training", {}) or {}
        self._post_training = load_handle(self._normalize_post_training(pt_raw))

    @property
    def post_training_handle(self) -> PostTrainingHandle:
        return self._post_training

    def execution_blocker(self, project: str) -> str:
        if not self.loop_policy.completion_driven:
            return ""
        from app.harness.tools.project_repo import load_project_repo
        repo = load_project_repo(project)
        if repo.read_only:
            return "编码实验分支未就绪：请通过研究任务启动，系统会在原 Git 仓库创建并绑定任务分支；未调用模型。"
        if not repo.root.is_dir():
            return "编码工作目录不存在；未调用模型。"
        return ""

    async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
        blocker = self.execution_blocker(request.project)
        if blocker:
            raise ValueError(blocker)
        return await super().run_loop(request, context)

    async def build_context(self, request: RunRequest) -> ContextPack:
        context = await super().build_context(request)
        from app.harness.tools.config import check_commands
        context.upstream['configured_code_checks'] = json.dumps({
            kind: [{'command_id': cmd.id, 'argv': list(cmd.argv)} for cmd in check_commands(kind)]
            for kind in ('lint', 'test')})
        context.upstream['configured_code_checks_usage'] = (
            'code.lint/code.test_runner 的 command_id 必须使用上述 MARS 配置 ID，不能填写 shell 命令。'
            '省略 command_id 会执行该类全部检查；仅在任务确实需要全量回归时才省略。'
            '纯YAML参数复制不新增测试文件、不重复全仓测试，保留各组真实入口的不训练预检。'
            '需要检查时选择最相关command_id一次；同run相同Git可见内容快照、命令与实际环境的数据绑定，'
            'MARS可返回reused=true和原成功receipt。引用它说明复用，不声称新进程执行；输入变化或失败必须实跑。'
            '测试中切换全局配置时先清空旧配置，'
            '避免上一组残留的 scheduler 等字段污染下一组。')
        from app.harness.tools.execution.handoff import execution_delivery_context
        context.upstream['configured_execution_delivery'] = json.dumps(
            execution_delivery_context(), ensure_ascii=False)
        from app.harness.schema.experiment_contract import document_hash, document_metadata
        for supplied in request.upstream_artifacts.values():
            text = supplied.split('\n', 1)[1] if supplied.startswith('[upstream artifact: ') else supplied
            if text.startswith('---\n') and document_metadata(text).get('schema') == 'experiment_plan.v1':
                context.upstream['approved_experiment_identity'] = json.dumps({
                    'experiment_plan_sha256': document_hash(text),
                    'instruction': '此哈希由 MARS 计算，直接用于编码交付；不得自行猜测或改变实验约束。'})
        return context

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if errors:
            return errors
        from app.harness.schema.frontmatter_parser import parse
        metadata = parse(text).metadata
        # The host validator has already checked the run's durable write ledger,
        # bound branch and current file hashes above. A retry may reuse those
        # genuine writes without issuing a redundant mutation in this invocation.
        # Standalone callers without that host verifier retain the local check.
        if request.candidate_validator is None and metadata.get("files_changed") and not any(
            item.get("ok") and item.get("tool") in {"code.apply_patch", "code.write_file", "code.delete_file", "code.copy_yaml"}
            for item in observations
        ):
            errors.append("/files_changed: implement the proposed changes through the real code tools before submitting")
        plans = []
        for supplied in request.upstream_artifacts.values():
            text = supplied.split("\n", 1)[1] if supplied.startswith("[upstream artifact: ") else supplied
            if text.startswith("---\n"):
                upstream = parse(text).metadata
                if upstream.get("schema") == "experiment_plan.v1":
                    plans.append(text)
        if plans or metadata.get('execution_jobs') or request.extra.get('execution_delivery_required'):
            from app.harness.tools.execution.handoff import coding_handoff_preflight_errors
            if len(plans) > 1:
                errors.append('/upstream: 必须有唯一的批准实验方案')
            else:
                errors.extend(await coding_handoff_preflight_errors(plans[0] if plans else '', metadata,
                    project=request.project, run_id=str(request.extra.get('run_id') or ''),
                    run_root=Path(str(request.extra.get('run_root') or '')),
                    attempt=request.extra.get('attempt', 1),
                    plan_required=bool(request.extra.get('experiment_plan_required'))))
        return errors

    def load_post_training(
        self, config: Mapping[str, object]
    ) -> PostTrainingHandle:
        self._post_training = load_handle(config, source="runtime")
        return self._post_training

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        settings = get_settings()
        # A persisted invocation chooses its recovery engine. A default change
        # must never reinterpret a native checkpoint as a ZCode session.
        backend = settings.mars_coding_backend
        invocation = request.extra.get("resume_invocation")
        if invocation:
            root = Path(str(request.extra["run_root"]))
            checkpoint = root / "agent_traces/coding" / str(invocation) / "checkpoint.json"
            data = json.loads(checkpoint.read_text())
            backend = "zcode" if data.get("backend") == "zcode" else "native_llm"
        if backend == "zcode":
            from app.harness.agent_loop.zcode.executor import ZCodeLoopExecutor
            schema = json.loads((repo_root() / "backend/app/harness/schema/schemas/code_spec.v1.json").read_text())
            schema["properties"]["project"] = {"type": "string", "const": request.project}
            return await self._draft_via_llm(request, context, executor=ZCodeLoopExecutor(), structured_schema=schema)
        if (
            backend == "opencode"
        ):
            adapter = OpenCodeAdapter()
            if not adapter.is_available():
                if settings.is_production:
                    raise RuntimeError(
                        "MARS_CODING_BACKEND=opencode but opencode is not installed"
                    )
                message = (
                    "MARS_CODING_BACKEND=opencode, but opencode is not installed. "
                    "Falling back to the governed native LLM Coding Agent; no mock "
                    "coding artifact is generated."
                )
                logger.warning(message)
                _write_backend_note(request, message)
                context.upstream["coding_backend_fallback"] = message
                context.metadata["coding_backend_fallback"] = "opencode_unavailable"
                return await self._draft_via_llm(request, context)

            result = await adapter.run(request, context)
            acceptable_statuses = {"completed", "completed_with_warnings"}
            if result.status not in acceptable_statuses:
                message = (
                    f"OpenCode backend failed with status={result.status}; "
                    f"transcript={result.transcript_path}; error={result.error[:500]}"
                )
                raise RuntimeError(message)
            files_changed = result.files_changed
            metadata: dict[str, Any] = {
                "schema": "code_spec.v1",
                "project": request.project,
                "agent": "coding",
                "upstream_artifact": _first_upstream_ref(request.upstream_artifacts),
                "target_lang": "python",
                "baseline_compat": {
                    "preserved": True,
                    "rationale": "External coding backend is sandboxed by a MARS task packet; patches still require code.apply_patch through ToolRegistry Gate 5.",
                },
                "files_changed": files_changed,
                "new_dependencies": [],
                "test_coverage": {
                    "unit_tests_added": 0,
                    "baseline_smoke_test": "skipped",
                },
                "coding_backend": {
                    "name": result.backend,
                    "status": result.status,
                    "task_packet": result.task_packet_path,
                    "transcript": result.transcript_path,
                    "diff": result.diff_path,
                    "checks": result.checks,
                    "diff_stats": result.diff_stats,
                    "error": result.error,
                },
            }
            if result.status == "completed_with_warnings":
                metadata["quality_warnings"] = [
                    "OpenCode changed files but did not finish cleanly; inspect transcript and patch before approval."
                ]
            stats = result.diff_stats
            changed = int(stats.get("files_changed", len(files_changed)) or 0)
            insertions = int(stats.get("insertions", 0) or 0)
            deletions = int(stats.get("deletions", 0) or 0)
            body = (
                "# Code Spec\n\n"
                f"- Backend: {result.backend}\n"
                f"- Status: {result.status}\n"
                f"- Code changes: {changed} files changed, +{insertions} -{deletions}\n"
                f"- Task packet: `{result.task_packet_path}`\n"
                f"- Transcript: `{result.transcript_path}`\n"
                f"- Diff: `{result.diff_path or 'none'}`\n\n"
                "Patch application remains blocked until HITL approval and "
                "`code.apply_patch` dispatch.\n"
            )
            return Artifact(
                text=fm_dumps(metadata, body),
                schema_id=self.output_schema,
                metadata=metadata,
                body=body,
            )
        return await self._draft_via_llm(request, context)

    def _select_provider(self) -> tuple[LLMProvider, LLMConfig]:
        handle = self._post_training
        if not handle.enabled or handle.mode != "endpoint":
            return super()._select_provider()

        cfg = LLMConfig(
            provider=handle.endpoint_provider,
            model=handle.model or self._config.model_name,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            response_schema=self.output_schema,
            extra={
                "post_training": {
                    "enabled": True,
                    "mode": handle.mode,
                    "source": handle.source,
                }
            },
        )

        settings = get_settings()
        try:
            api_key = env_or_local(handle.api_key_env or "")
            endpoint = handle.custom_endpoint or ""
            if handle.endpoint_provider == "local_vllm":
                if not _endpoint_reachable(endpoint):
                    if settings.is_production:
                        raise RuntimeError(
                            f"coding post-training endpoint is unreachable: {endpoint}"
                        )
                    logger.warning(
                        "coding post-training endpoint {} is unreachable; "
                        "falling back to configured provider {}",
                        endpoint,
                        self._config.model_provider,
                    )
                    return super()._select_provider()
                return (
                    LocalVllmProvider(
                        base_url=endpoint,
                        api_key=api_key or "EMPTY",
                    ),
                    cfg,
                )
            return (
                CustomEndpointProvider(api_key=api_key, base_url=endpoint),
                cfg,
            )
        except Exception as exc:
            raise RuntimeError("coding post-training provider failed to initialize") from exc

    @staticmethod
    def _normalize_post_training(raw: object) -> Mapping[str, object]:
        if not isinstance(raw, Mapping):
            return {}
        return {str(k): v for k, v in raw.items()}


def _first_upstream_ref(upstream: dict[str, str]) -> str:
    if not upstream:
        return ""
    return next(iter(upstream.keys()))


def _endpoint_reachable(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _write_backend_note(request: RunRequest, message: str) -> None:
    agent_dir = str(request.extra.get("agent_dir") or "")
    run_root = str(request.extra.get("run_root") or "")
    if agent_dir:
        target_dir = Path(agent_dir)
    elif run_root:
        target_dir = Path(run_root) / "coding"
    else:
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "coding_backend_fallback.md").write_text(
            "# Coding backend fallback\n\n"
            f"{message}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("failed to write coding backend fallback note: {}", exc)
