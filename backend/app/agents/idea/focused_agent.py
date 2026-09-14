"""The normal Idea service path: read methods, propose, then cross-model review."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Any
import uuid

import yaml

from app.agents.base import BaseAgent, ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.acceptance import archive_baseline_input
from app.agents.idea.delivery import delivery_errors
from app.agents.idea.focused_research import focused_research_errors, focused_requirement_errors, research_schema
from app.agents.idea.runtime_profile import public_agent_configuration
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import LLMConfig, LLMProvider, Message
from app.harness.schema.frontmatter_parser import parse
from app.settings import repo_root, get_settings


BRIEF = """根据项目固定知识、当前任务、真实代码和数据说明，提出有文献依据的可尝试方案。
先明确需要解决的信息缺口，再自主检索相近方法、直接基线和适用限制。不要按固定篇数停止，
也不要追求全面收集。只有核心问题覆盖充分、采用的方法理解完整、剩余不确定性已说明时才形成方案。
摘要只能用于筛选。采用的核心论文必须实际读完方法步骤、关键公式、假设与依赖的附录，
并查看与判断有关的实验和限制。工具返回的是阅读窗口，PDF保存成功不等于方法已读懂。
使用 start_page/max_pages/char_offset 连续阅读，检查截断和 next_offset；公式缺失不得凭记忆补造。
已下载资料续读时sources只需填工具返回的source_id，宿主自动还原标题和地址。CVF/NeurIPS正式论文优先使用对应官方工具的PDF地址。
检索词保持简短，先用OpenAlex发现相关文献；CVF/NeurIPS按标题词匹配，不能把长自然语言问题当查询。
一个检索接口限流或不可用时切换来源，不在同一个失败接口反复改词消耗预算。
可以使用全文HTML，不能把摘要网页当完整方法。保留PDF供人复查；失败时优先切换已经找到的官方来源，
复用成功下载的文件，不重复下载。工具提供 source_id、路径和版本，不要手写文件哈希或阅读收据。
候选文献可以采用、排除、待补充，解释各自与任务的具体联系。方法是否可迁移，要结合项目代码、数据和约束判断。
最终只提出一个方案，明确基线改动、输入输出、关键公式/步骤、初始化或优化方式、适用条件与最小验证办法。
选定一个可以立即尝试的主方案，不要求同时实现多个变体。关键公式须处理边界、重复值、除零和有限精度等退化情形。
提交前逐项核对公式、步骤、初始化、handoff和摘要是否描述同一个实现。区分保留外部接口与修改内部算法，
区分参数形状兼容与迁移后函数等价；涉及状态迁移时明确采用重新初始化、映射还是拟合，并解释适用条件。
收到评审后修订整份方案及所有关联字段，删除失效的旧说法；不要只在被点名的字段旁追加补丁。
没有实测收益或创新性证明可以形成假设，不得声称已有实验成功。不要自动生成庞大的统计协议或固定数量消融。
所有解释用清楚的中文，字段保留schema要求的名称。
将完整方法只定义在 method_spec；handoff.changes[].spec_ref 指向其具体子字段，
verification_requirements[].decision_rule_ref 指向 /decision_rule。
human_summary写1至2句中文概括，body逐字复制human_summary；详细方案由界面从结构字段呈现。
research_context记录实际选文原则、候选和结束理由。采用项引用fetch工具返回的source_id，
method_sections列出实际读过的完整方法章节，method_summary复述原方法，
transfer说明如何影响本方案，method_spec_ref绑定对应设计，limitations说明迁移假设及局限。
被排除或待补充的来源可把source_id设为空字符串，但url必须来自实际工具结果。
research_context不是旧的research_assessment/research_links，不生成委派报告。
提出初稿之后，由另一模型复核原任务、完整方法阅读材料和候选；在宿主给定的总调用和评审预算内修订后复核。
关键证据无法获取时明确报告缺口并停止，不把预算用尽说成调研完成。"""


class FocusedIdeaAgent(IdeaAgent):
    agent_brief = BRIEF

    def __init__(self) -> None:
        path = repo_root() / "configs/idea_focused.yaml"
        settings = yaml.safe_load(path.read_text())
        if not get_settings().mars_web_search_provider:
            settings["tools"] = [tool for tool in settings["tools"] if tool != "search.web_search"]
        original = get_agent_config(settings.get("author_agent", "idea"))
        raw = deepcopy(dict(original.raw))
        raw["loop"], raw["tools"] = settings["loop"], settings["tools"]
        self._review_config = get_agent_config(settings["review_agent"])
        if (original.model_provider, original.model_name) == (self._review_config.model_provider, self._review_config.model_name):
            raise ValueError("focused Idea requires different generation and review models")
        author = replace(original, tools=tuple(settings["tools"]), raw=raw, debate_enabled=False,
                         **settings["author"])
        if (author.thinking_enabled and settings["loop"]["protocol"] == "native_tools"
                and not settings["loop"].get("native_observation_history")):
            raise ValueError("thinking author requires explicit observation history for native tools")
        super().__init__(agent_config=author)
        self._snapshot = {"schema": "idea.focused.runtime.v1", "profile_id": "focused_v1",
                          "source_sha256": digest(settings), "author": public_agent_configuration(author),
                          "reviewer": public_agent_configuration(self._review_config)}
        from app.harness.tools.registry import get_registry
        get_registry().scope_for_read_tools(self.name, self.config.tools)

    def configured_read_tools(self) -> tuple[str, ...]:
        return self.config.tools

    @property
    def service_profile_snapshot(self) -> dict[str, Any]:
        return deepcopy(self._snapshot)

    def _select_review_provider(self) -> tuple[LLMProvider, LLMConfig]:
        return select_provider(self._review_config)

    def requires_research_dossier(self, request: RunRequest) -> bool:
        return False

    def required_review_tools(self, request: RunRequest) -> tuple[str, ...]:
        return ("search.fetch_sources",)

    async def build_context(self, request: RunRequest) -> ContextPack:
        root = Path(str(request.extra["run_root"]))
        path = root / "input/idea_focused.v1.json"
        if path.exists():
            if json.loads(path.read_text()) != self._snapshot:
                raise ValueError("focused Idea configuration changed; start a new run")
        else:
            if request.extra.get("resume_invocation") or any((root / "agent_traces").glob("*/*/checkpoint.json")):
                raise ValueError("cannot change a historical run to focused Idea")
            atomic_json(path, self._snapshot)
        requirements = request.extra.get("idea_requirements", {})
        if requirements.get("require_research_dossier"):
            raise ValueError("full delegated research dossier requires the explicit legacy research profile")
        context = await BaseAgent.build_context(self, request)
        context.task += "\n可获取正文的域名：" + get_settings().mars_web_search_allowlist
        scope = request.extra.get("scope", "method_proposal")
        context.task += "\n本次范围：" + str(scope) + "\n用户明确的约束：" + json.dumps(requirements, ensure_ascii=False)
        if requirements.get("require_parameter_budget") or requirements.get("max_parameter_ratio"):
            context.task += ("\nparameter_budget须使用unit=real_scalar，数值variables，baseline_formula/candidate_formula，"
                "baseline_parameters/candidate_parameters整数，以及baseline_components/candidate_components列表。"
                "每项含name,formula,dtype(real或complex),shape；复数按两个实数计数。公式只能引用variables。"
                "不必为了参数约束增加固定数量的备选、消融或完整统计协议。")
        if scope == "method_proposal":
            context.task += "\n当前交付方法提案。handoff.required_context列出baseline_code和data_description为实际执行的前置条件。"
        else:
            context.task += "\n读取实际基线代码；知识文档中的代码快照需要与当前源码核对，不能只凭仓库路径声称已读代码。"
        context.metadata["idea_runtime_profile"] = {"profile_id": "focused_v1", "configuration_sha256": digest(self._snapshot)}
        return context

    def submission_schema(self, request: RunRequest) -> dict[str, Any] | None:
        schema = BaseAgent.submission_schema(self, request)
        assert schema is not None
        schema["required"] += ["human_summary", "handoff", "method_spec", "decision_rule", "research_context"]
        for field in ("method_spec", "decision_rule"):
            schema["properties"][field] = {"type": "object", "minProperties": 1}
        schema["properties"]["research_context"] = research_schema()
        schema["properties"]["handoff"]["properties"]["scope"] = {
            "const": request.extra.get("scope", "method_proposal")}
        req = request.extra.get("idea_requirements", {})
        if req.get("require_parameter_budget") or req.get("max_parameter_ratio"):
            schema["required"].append("parameter_budget")
            schema["properties"]["parameter_budget"] = {"type": "object", "minProperties": 1}
        if req.get("require_evaluation_protocol"):
            from app.agents.idea.protocol import protocol_schema
            schema["required"].append("evaluation_protocol")
            schema["properties"]["evaluation_protocol"] = protocol_schema()
        return schema

    async def validate_candidate(self, request: RunRequest, text: str, observations: list[dict[str, Any]]) -> list[str]:
        errors = await BaseAgent.validate_candidate(self, request, text, observations)
        if errors:
            return errors
        parsed = parse(text)
        root = Path(str(request.extra["run_root"]))
        scope = str(request.extra.get("scope", "method_proposal"))
        errors += delivery_errors(parsed.metadata, scope, body=parsed.body)
        errors += focused_research_errors(parsed.metadata, observations, root)
        requirements = request.extra.get("idea_requirements", {})
        errors += focused_requirement_errors(parsed.metadata, observations, requirements)
        input_receipt = archive_baseline_input(run_root=root, project=request.project,
            content=request.upstream_artifacts.get("baseline_code", ""), candidate_sha256=digest(text))
        if scope == "project_proposal" and input_receipt is None and not any(o.get("ok") and o.get("tool") == "code.repo_reader" for o in observations):
            errors.append("/scope: project proposal requires reading the actual baseline code")
        atomic_json(root / "idea/validation" / (uuid.uuid4().hex + ".json"), {
            "schema_valid": True, "material_ready": not errors, "errors": errors, "candidate_sha256": digest(text),
            "requirements": requirements, "delivery_contract_version": "idea.handoff.v1", "body_policy": "summary_only",
            "research_contract": "idea.research_context.v1", "runtime_profile_sha256": digest(self._snapshot),
            "scope": scope, "input_evidence": [input_receipt] if input_receipt else [],
            "research_dossier_required": False, "research_assessment_required": False,
            "scientific_validated": False, "project_ready": False})
        return errors

    def review_messages(self, request: RunRequest, context: ContextPack) -> list[Message]:
        messages = [
            Message("system", "你是独立会话中的方法评审者，使用与生成者不同的模型。"
                "基于原任务、当前项目知识、实际原文阅读窗口和候选方案检查，不把生成者的解释当作论文事实。"
                "判断核心方法是否真正读完整、选文是否相关有用、迁移假设是否合理、关键公式和实现是否自洽。"
                "必须检查边界和退化输入，例如重复值、零分母、饱和区和有限精度；给出明确反例时要求修正。所有意见用中文。"
                "首次评审尽量一次列全实质问题，交叉核对公式、步骤、初始化、handoff与摘要的一致性；"
                "特别区分接口保留与内部算法变化、形状兼容与函数等价。复核时检查整份修订是否消除了矛盾。"
                "摘要或截断前缀不足以支持完整方法时，指出缺少的章节或公式；不要求无关段落全部阅读。"
                "不要求固定文献数量，不要求先取得实验收益，不额外要求完整实验统计设计。"
                "允许提出论文未直接给出的新组合或参数化，但必须明确区分已读原方法与作者的新设计，"
                "核对迁移依据、推导与适用条件；不能仅因参数化不是论文原实现就拒绝，也不能把脚注冒充完整方法。"
                "重大问题给出准确字段和原文依据；次要改进写在rationale里，不无限扩大范围。"),
            Message("user", "原始研究任务：\n" + request.user_request),
            Message("user", "项目背景与约束：\n" + context.project),
            Message("user", "本次显式要求：\n" + json.dumps(request.extra.get("idea_requirements", {}), ensure_ascii=False))]
        messages += [Message("user", "输入资料 " + label + ":\n" + content) for label, content in context.upstream.items()]
        return messages

    def reflection_rubric(self) -> str:
        return ("Check research_context against actual reading observations, not just author summaries. "
                "Require the complete operative method and relevant dependencies for adopted sources. "
                "Check task coverage, stopping reason, selection decisions, adaptation assumptions and implementability. "
                "Return concrete blockers only; experiments and global novelty proof belong downstream. "
                "Do not prescribe a fixed paper count. A different-model review is not experimental validation.")
