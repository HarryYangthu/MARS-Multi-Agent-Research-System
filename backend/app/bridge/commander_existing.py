"""Bounded, current-source admission for an existing model's execution request.

This resolves host-declared capabilities; it never imports research code, runs
training, changes scientific settings, or treats an old result as a new result.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
from fnmatch import fnmatchcase
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
from typing import Any

import yaml

from app.harness.tools.project_repo import ProjectRepo, resolve_allowed_path

MAX_CONFIGURATIONS = 32
MAX_DIRECTORY_ENTRIES = 256
MAX_SOURCE_BYTES = 131072
MAX_CAPABILITIES = 64
MAX_PATTERN_DEPTH = 6
MAX_SEED_DEFAULTS = 8


def _relative(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\x00" in value:
        raise ValueError("配置和入口必须是代码仓内的非空相对路径。")
    name = value.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or name in {".", ".."}:
        raise ValueError("配置和入口不能逃逸当前代码仓。")
    return path.as_posix()


def _patterns(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_CAPABILITIES:
        return []
    result = []
    for value in raw:
        name = _relative(value)
        if "**" in name or len(PurePosixPath(name).parts) > MAX_PATTERN_DEPTH:
            return []
        result.append(name)
    return result


def _profiles(project: str, registrations: Any) -> list[dict[str, Any]]:
    if not isinstance(registrations, list) or len(registrations) > MAX_CAPABILITIES:
        return []
    result = []
    for row in registrations:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            continue
        projects = row.get("project_ids")
        if not isinstance(projects, list) or project not in projects:
            continue
        configs, entries = _patterns(row.get("config_paths")), _patterns(row.get("entrypoints"))
        if configs and entries:
            result.append({"id": row["id"], "config_paths": configs, "entrypoints": entries,
                           **{key: row[key] for key in ("seed_policy", "seed_defaults") if key in row}})
    return result


def _read(repo: ProjectRepo, relative: str, purpose: str) -> tuple[str, dict[str, Any]]:
    # The ordinary repository policy remains authoritative. Explicitly forbid
    # symlink components so inspecting a name cannot grant another tree access.
    current = repo.root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("快捷准备不能读取符号链接；请选择当前仓库中的实际文件。")
    path = resolve_allowed_path(repo, relative, require_exists=True, require_text=True)
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("源文件超出快捷核对范围；请通过普通编码流程按需检查。")
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("源文件超出快捷核对范围。")
    text = data.decode("utf-8")
    return text, {"path": relative, "sha256": hashlib.sha256(data).hexdigest(),
                  "bytes": len(data), "line_start": 1, "line_end": max(1, len(text.splitlines())),
                  "purpose": purpose}


def _config_names(repo: ProjectRepo, profiles: list[dict[str, Any]]) -> list[str]:
    """Walk only declared pattern segments, with a strict total entry bound."""
    found: set[str] = set()
    visited: dict[str, list[tuple[str, bool]]] = {}
    inspected = 0
    for pattern in sorted({p for row in profiles for p in row["config_paths"]}):
        pending = deque([(Path("."), PurePosixPath(pattern).parts)])
        while pending:
            relative, parts = pending.popleft()
            if not any(char in parts[0] for char in "*?["):
                children = [(parts[0], len(parts) > 1)]
            else:
                key = relative.as_posix()
                if key not in visited:
                    directory = repo.root / relative
                    components = [repo.root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1)]
                    if not directory.is_dir() or any(path.is_symlink() for path in components):
                        continue
                    children = []
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            inspected += 1
                            if inspected > MAX_DIRECTORY_ENTRIES:
                                raise ValueError("登记配置范围较大，请提供明确配置路径，避免扫描整个代码仓。")
                            if not entry.is_symlink():
                                children.append((entry.name, entry.is_dir(follow_symlinks=False)))
                    visited[key] = sorted(children)
                children = visited[key]
            for name, is_directory in children:
                if not fnmatchcase(name, parts[0]):
                    continue
                child = relative / name
                if len(parts) > 1:
                    if is_directory and not (repo.root / child).is_symlink():
                        pending.append((child, parts[1:]))
                elif not is_directory and child.suffix.lower() in {".yaml", ".yml", ".json"}:
                    found.add(child.as_posix())
                    if len(found) > MAX_CONFIGURATIONS:
                        raise ValueError("登记配置超过快捷核对上限，请提供明确配置路径。")
    return sorted(found)


def _name(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold())


def _literal_selector(value: str, text: str, *, path: bool = False) -> bool:
    boundary = r"A-Za-z0-9_./-" if path else r"A-Za-z0-9_"
    return re.search(r"(?<![" + boundary + r"])" + re.escape(value) + r"(?![" + boundary + r"])",
                     text.replace("\\", "/"), re.IGNORECASE) is not None


def _validate_user_selector(chosen: dict[str, Any], selectors: list[dict[str, Any]], texts: list[str]) -> None:
    for text in texts:
        for row in selectors:
            name = row["model_name"]
            if (isinstance(name, str) and _literal_selector(name, text)
                    and name.casefold() != str(chosen["model_name"]).casefold()):
                raise ValueError("用户明确指定的实际模型名与提取结果不一致；请确认模型，不能运行另一个模型。")
            relative = row["config_path"]
            if (relative != chosen["config_path"] and (_literal_selector(relative, text, path=True)
                    or _literal_selector(Path(relative).name, text, path=True))):
                raise ValueError("用户明确指定的实际配置路径与提取结果不一致；请确认配置，不能替换运行。")


def _mentioned_configurations(texts: list[str], profiles: list[dict[str, Any]]) -> list[str]:
    """Resolve only literal user paths under declared static pattern prefixes."""
    paths: set[str] = set()
    patterns = {p for row in profiles for p in row["config_paths"]}
    for text in texts:
        for match in re.finditer(r"[A-Za-z0-9_.-]+(?:[/\\][A-Za-z0-9_.-]+)*\.(?:yaml|yml|json)(?![A-Za-z0-9_.-])", text, re.IGNORECASE):
            try:
                name = _relative(match.group())
            except ValueError:
                continue
            variants = {name}
            if "/" not in name:
                for pattern in patterns:
                    parent = PurePosixPath(pattern).parent.as_posix()
                    if not any(char in parent for char in "*?["):
                        variants.add((PurePosixPath(parent) / name).as_posix())
            paths.update(value for value in variants if any(fnmatchcase(value, p) for p in patterns))
            if len(paths) > MAX_CONFIGURATIONS:
                raise ValueError("本次消息涉及多个配置，请明确一个运行配置。")
    return sorted(paths)


def _seed_configuration(
    repo: ProjectRepo, chosen: dict[str, Any], entry: str,
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    """Follow only the host-declared loader order; never infer a base file."""
    profiles = [row for row in chosen["profiles"] if any(fnmatchcase(entry, p) for p in row["entrypoints"])]
    declarations: list[tuple[Any, list[str] | None]] = []
    for row in profiles:
        defaults = row.get("seed_defaults")
        if "seed_defaults" in row:
            if not isinstance(defaults, list) or len(defaults) > MAX_SEED_DEFAULTS:
                raise ValueError("登记的seed_defaults必须是有界、按实际加载顺序排列的相对文件路径列表。")
            defaults = [_relative(value) for value in defaults]
            if any(any(char in value for char in "*?[") for value in defaults):
                raise ValueError("seed_defaults必须声明实际文件，不能通过模式猜测默认配置。")
        declarations.append((row.get("seed_policy"), defaults))
    if any(value != declarations[0] for value in declarations[1:]):
        # Entry/config registrations disagree about inheritance. Preserve the
        # ordinary path rather than picking a policy or an inherited seed.
        return chosen["configuration"].get("seed"), {
            "policy": None, "defaults_declared": False, "reason": "seed_registration_ambiguous",
            "merge_order": [chosen["config_path"]]}, []
    policy, defaults = declarations[0]
    seed: Any = None
    evidence = []
    values = []
    for relative in defaults or []:
        text, read = _read(repo, relative, "seed_defaults")
        try:
            configuration = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError("登记的默认配置无法解析，不能推断seed默认值。") from exc
        if not isinstance(configuration, dict):
            raise ValueError("登记的默认配置必须是实际可读取的配置对象。")
        values.append({"path": relative, "has_seed": "seed" in configuration,
                       **({"seed": configuration["seed"]} if "seed" in configuration else {})})
        if "seed" in configuration:
            seed = configuration["seed"]
        evidence.append(read)
    configuration = chosen["configuration"]
    if "seed" in configuration:
        seed = configuration["seed"]
    values.append({"path": chosen["config_path"], "has_seed": "seed" in configuration,
                   **({"seed": configuration["seed"]} if "seed" in configuration else {})})
    return seed, {"policy": policy, "defaults_declared": defaults is not None,
                  "merge_order": [*(defaults or []), chosen["config_path"]], "values": values}, evidence


def materialize_existing_seed(resolved: dict[str, Any]) -> dict[str, Any]:
    """Allocate only a new task's seed, after actual user intent is validated."""
    result = deepcopy(resolved)
    if result.get("seed_generation_required"):
        if result.get("default_seed") is not None or result["requested"].get("seed") is not None:
            raise ValueError("生成种子不能覆盖用户或实际配置中的种子。")
        provenance = result.get("seed_provenance", {})
        if provenance.get("policy") != "generate_if_unset" or provenance.get("defaults_declared") is not True:
            raise ValueError("生成种子必须有明确登记的默认配置加载策略。")
        result["requested"]["seed"] = secrets.randbits(32)
        result["seed_source"] = "generated_for_run"
        result["seed_generation_required"] = False
    return result


def resolve_existing_request(
    repo: ProjectRepo, registrations: Any, args: dict[str, Any], *, user_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve a single current configuration, or return actual choices/no claim.

    Arguments are intent, not evidence. Only bounded real file reads establish
    the configuration and entrypoint; the Coding host still performs preflight.
    """
    allowed = {"model_name", "config_path", "entrypoint", "max_iters", "budget_unit", "seed",
               "parameter_overrides", "start", "stop_after_execution"}
    if set(args) - allowed:
        raise ValueError("已有模型工具仅接受模型/配置、入口、预算、种子及明确参数；不能指定其他项目、命令或输出路径。")
    if not isinstance(args.get("budget_unit"), str) or args["budget_unit"] not in {"steps", "epochs"}:
        raise ValueError("请明确训练预算单位：steps为优化器更新，epochs为完整训练轮次。")
    if type(args.get("max_iters")) is not int or args["max_iters"] < 1:
        raise ValueError("训练预算必须是正整数。")
    if "start" in args and type(args["start"]) is not bool:
        raise ValueError("start必须为布尔值。")
    if "stop_after_execution" in args and type(args["stop_after_execution"]) is not bool:
        raise ValueError("stop_after_execution必须为布尔值。")
    overrides = args.get("parameter_overrides", {})
    if not isinstance(overrides, dict) or any(not isinstance(key, str) for key in overrides):
        raise ValueError("parameter_overrides必须为明确的配置键和值对象。")
    model = args.get("model_name", "")
    if not isinstance(model, str) or len(model) > 160:
        raise ValueError("请提供准确模型名称或配置路径。")
    model = model.strip()
    if model and not _name(model):
        raise ValueError("请提供有效模型名称或配置路径。")
    explicit = _relative(args["config_path"]) if args.get("config_path") else ""
    if not explicit and not model:
        raise ValueError("请提供模型名称或准确配置路径。")
    profiles = _profiles(repo.project, registrations)
    if explicit and not any(any(fnmatchcase(explicit, p) for p in row["config_paths"]) for row in profiles):
        text, read = _read(repo, explicit, "configuration")
        try:
            configuration = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError("当前配置无法解析，请核对实际配置内容。") from exc
        if not isinstance(configuration, dict):
            raise ValueError("当前配置必须是实际可读取的配置对象。")
        actual_model = configuration.get("model_name")
        return {"ok": False, "status": "execution_registration_required", "config_path": explicit,
                "model_name": actual_model if isinstance(actual_model, str) else None, "evidence": [read],
                "training_started": False,
                "error": "实际配置已读取，但当前项目尚未登记该配置的执行入口/范围；请补充执行登记，或由普通Coding准备交付。这不表示模型没有实现。"}
    if not profiles:
        return {"ok": False, "status": "capability_unavailable",
                "error": "当前项目尚无明确登记的配置/入口范围；请从普通Coding核对并准备已有模型执行。"}
    names = [explicit] if explicit else _config_names(repo, profiles)
    candidates: list[dict[str, Any]] = []
    selectors: list[dict[str, Any]] = []
    for relative in names:
        matches = [row for row in profiles if any(fnmatchcase(relative, p) for p in row["config_paths"])]
        if not matches:
            continue
        try:
            text, evidence = _read(repo, relative, "configuration")
            configuration = yaml.safe_load(text)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            if explicit:
                raise ValueError("当前配置不可在快捷范围内读取解析；请核对路径或使用普通Coding。") from None
            continue
        if not isinstance(configuration, dict):
            continue
        actual_model = configuration.get("model_name")
        if not isinstance(actual_model, str):
            actual_model = None
        selectors.append({"config_path": relative, "model_name": actual_model})
        aliases = [Path(relative).stem] + ([actual_model] if isinstance(actual_model, str) else [])
        if model and not any(_name(model) in _name(alias) for alias in aliases):
            continue
        candidates.append({"config_path": relative, "model_name": actual_model,
                           "configuration": configuration, "evidence": evidence, "profiles": matches,
                           "exact": any(_name(model) == _name(alias) for alias in aliases) if model else True})
    exact = [row for row in candidates if row["exact"]]
    if exact:
        candidates = exact
    if len(candidates) != 1:
        choices = [{"config_path": row["config_path"], "model_name": row["model_name"]} for row in candidates]
        return {"ok": False, "status": "model_selection_required" if choices else "model_not_resolved",
                "candidates": choices, "error": "请从实际匹配配置中选择一个。" if choices else
                "登记配置中未唯一确认该模型；这不证明模型不存在。请提供配置路径，或按普通Coding读取相关源码核对。"}
    chosen = candidates[0]
    if user_texts:
        if explicit:
            # An explicit path must still bypass catalog enumeration. Check
            # only other configuration paths literally named by this user.
            for relative in _mentioned_configurations(user_texts, profiles):
                if relative == explicit:
                    continue
                try:
                    text, _ = _read(repo, relative, "configuration_identity")
                    config = yaml.safe_load(text)
                except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                    continue
                if isinstance(config, dict):
                    model_name = config.get("model_name")
                    selectors.append({"config_path": relative,
                                      "model_name": model_name if isinstance(model_name, str) else None})
        _validate_user_selector(chosen, selectors, user_texts)
    entries = sorted({value for row in chosen["profiles"] for value in row["entrypoints"]
                      if not any(char in value for char in "*?[")})
    entry = _relative(args["entrypoint"]) if args.get("entrypoint") else entries[0] if len(entries) == 1 else ""
    if not entry or not any(any(fnmatchcase(entry, p) for p in row["entrypoints"]) for row in chosen["profiles"]):
        return {"ok": False, "status": "entrypoint_selection_required", "entrypoints": entries,
                "error": "请确认当前登记的训练入口，不猜测运行命令。"}
    _, entry_evidence = _read(repo, entry, "entrypoint")
    default_seed, seed_provenance, seed_evidence = _seed_configuration(repo, chosen, entry)
    explicit_seed = args.get("seed") is not None
    seed = args["seed"] if explicit_seed else default_seed
    if seed is not None and (type(seed) is not int or seed < 0):
        raise ValueError("seed必须是实际用户指定或配置中的非负整数；不能以生成策略替换无效值。")
    generate_seed = (seed is None and seed_provenance.get("policy") == "generate_if_unset"
                     and seed_provenance["defaults_declared"])
    missing_seed = seed is None and not generate_seed
    ordinary_reason = seed_provenance.get("reason") or ("seed_default_unresolved" if missing_seed else None)
    requested = {"config_path": chosen["config_path"], "entrypoint": entry, "seed": seed,
                 "max_iters": args["max_iters"], "budget_unit": args["budget_unit"]}
    return {"ok": True, "status": "resolved", "requested": requested,
            "model_name": chosen["model_name"], "source_root": str(repo.root.resolve()),
            "evidence": [chosen["evidence"], *seed_evidence, entry_evidence],
            "default_seed": default_seed, "seed_provenance": seed_provenance,
            "seed_source": "user_request" if explicit_seed else "config_default" if seed is not None else
                           "generated_for_run" if generate_seed else "not_configured",
            "seed_generation_required": generate_seed,
            "ordinary_coding_reason": ordinary_reason,
            "default_budget": {key: chosen["configuration"].get(key) for key in ("budget_unit", "max_iters")},
            "ordinary_coding": bool(overrides) or bool(ordinary_reason), "parameter_overrides": overrides,
            "training_started": False, "preflight_passed": False}


def _budget_count(raw: str) -> int | None:
    """Parse explicit common Chinese integers, rejecting shorthand ambiguity."""
    if raw.isdecimal():
        return int(raw)
    digits = {char: value for value, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if any(char not in digits and char not in units for char in raw):
        return None
    total = section = number = 0
    previous_digit = False
    last_unit = 100000
    zero_after_unit = False
    seen_wan = False
    for index, char in enumerate(raw):
        if char in digits:
            value = digits[char]
            if previous_digit and number != 0:
                return None
            number, previous_digit = value, True
            if value == 0:
                zero_after_unit = True
            continue
        unit = units[char]
        if unit == 10000:
            if seen_wan or section + number == 0:
                return None
            total, section, number = (section + number) * unit, 0, 0
            seen_wan = True
            last_unit = unit
        else:
            if unit >= last_unit or (number == 0 and not (index == 0 and unit == 10)):
                return None
            section += (number or 1) * unit
            last_unit, number = unit, 0
        previous_digit = False
        zero_after_unit = False
    if number and last_unit >= 100 and last_unit != 100000 and not zero_after_unit:
        return None  # e.g. 一百二 / 一万一 can denote two different quantities.
    return total + section + number


class ExistingIntentValidationError(ValueError):
    """A deterministic admission result; another identical model call cannot fix it."""

    def __init__(
        self, code: str, message: str, *, requires_user_input: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = False
        self.requires_user_input = requires_user_input
        self.details = details or {}

    def structured_fields(self) -> dict[str, Any]:
        return {"error_code": self.code, "retryable": self.retryable,
                "requires_user_input": self.requires_user_input, "details": self.details}


def _intent_prefix(text: str, start: int) -> str:
    """Keep nearby clause scope, so 'not X, but Y' cannot negate Y."""
    prefix = re.split(r"[，,。;；!?！？\n（）()]", text[:start])[-1]
    # A contrast ends a preceding negation even without punctuation.
    return re.split(r"而是|而要|但(?:是)?|\bbut\b", prefix, flags=re.IGNORECASE)[-1][-80:]


def _negated_intent(text: str, start: int, end: int) -> bool:
    prefix = _intent_prefix(text, start)
    negative = re.search(
        r"(?:不是|并非|而非|不要|不用|无需|不需要|不跑|不训练|(?<![分特个类识辨区])别|不应|不应该|禁止)"
        r"\s*(?:(?:只|仅|再|只要|把|将|设为|设置为|改成|改为|运行|训练|跑|执行)\s*)*$|"
        r"\b(?:not|never|rather\s+than|instead\s+of|do\s+not|don't)\s*"
        r"(?:(?:just|only|run|train|use|set|to|exactly)\s+)*$",
        prefix, re.IGNORECASE,
    )
    # An explicit postfix rejection is also evidence, not an alternative budget.
    suffix = text[end:]
    rejected = re.match(r"\s*(?:不对|不正确|作废|不要|不用|取消|是错的|不算|is\s+wrong\b)",
                        suffix, re.IGNORECASE)
    return negative is not None or rejected is not None


def _corrects_previous_intent(text: str, start: int) -> bool:
    prefix = _intent_prefix(text, start)
    # Keep these explicit. A later mention alone must not resolve '2 or 3 epochs'.
    return re.search(
        r"(?:改成|改为|改跑|改用|改回|换成|更正为|纠正为|调整为|实际(?:是|要|跑)|而是)"
        r"\s*(?:(?:只|仅|再|训练|运行|跑|执行)\s*)*$|"
        r"\b(?:actually|instead|change\s+to|correct\s+to)\s*"
        r"(?:(?:run|train|use|to|exactly)\s+)*$",
        prefix, re.IGNORECASE,
    ) is not None or re.search(r"(?:而是|而要)\s*(?:训练|运行|跑|run|train|use)?\s*$",
                              text[:start], re.IGNORECASE) is not None


def _budget_mentions(text: str, *, allow_unqualified_rounds: bool = True) -> list[tuple[int, int, str, int]]:
    number = r"(?<![\d.\-零〇一二两三四五六七八九十百千万负])([\d零〇一二两三四五六七八九十百千万]+)"
    found: set[tuple[int, int, str, int]] = set()
    round_unit = r'轮(?:训练)?' if allow_unqualified_rounds else r'轮训练'
    for unit, patterns in (
        ("steps", (number + r"\s*(?:步|(?:次|个)?\s*(?:优化器\s*)?(?:更新|steps?(?![A-Za-z])|optimizer\s+updates?))",
                   r"(?:max[_-]steps)\s*[:=：]\s*(\d+)(?![\d.])")),
        ("epochs", (number + rf"\s*(?:个\s*)?(?:完整\s*)?(?:epochs?(?![A-Za-z])|{round_unit})",
                    r"\bepochs?\s*[:=：]\s*(\d+)(?![\d.])")),
    ):
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                count = _budget_count(match.group(1))
                if count is not None:
                    found.add((match.start(), match.end(), unit, count))
    return sorted(found)


def validate_existing_user_intent(resolved: dict[str, Any], user_texts: list[str], *,
                                  allow_unqualified_rounds: bool = True) -> None:
    """Validate extracted values against actual user text, not a rewritten goal.

    This is a consistency check after one model interpretation, not a regex
    router. An unlabelled repetition count never becomes an optimizer budget.
    """
    budgets: set[tuple[str, int]] = set()
    excluded_budgets: set[tuple[str, int]] = set()
    seeds: set[int] = set()
    excluded_seeds: set[int] = set()
    for text in user_texts:
        current_budgets: set[tuple[str, int]] = set()
        rejected_end: int | None = None
        for start, end, unit, count in _budget_mentions(text, allow_unqualified_rounds=allow_unqualified_rounds):
            mention = (unit, count)
            rejected_alternative = rejected_end is not None and re.fullmatch(
                r"\s*(?:或|和|以及|or|and)\s*", text[rejected_end:start], re.IGNORECASE) is not None
            if _negated_intent(text, start, end) or rejected_alternative:
                excluded_budgets.add(mention)
                current_budgets.discard(mention)
                budgets.discard(mention)
                rejected_end = end
                continue
            rejected_end = None
            if _corrects_previous_intent(text, start):
                current_budgets.clear()
            current_budgets.add(mention)
            excluded_budgets.discard(mention)
        if current_budgets:
            budgets = current_budgets
        current_seeds: set[int] = set()
        for match in re.finditer(
            r"(?:(?<![A-Za-z0-9_])seed|种子)(?:设为|为)?\s*[:=：]?\s*(\d+)(?![\d.])", text, re.IGNORECASE,
        ):
            seed = int(match.group(1))
            if _negated_intent(text, match.start(), match.end()):
                excluded_seeds.add(seed)
                current_seeds.discard(seed)
                seeds.discard(seed)
                continue
            if _corrects_previous_intent(text, match.start()):
                current_seeds.clear()
            current_seeds.add(seed)
            excluded_seeds.discard(seed)
        if current_seeds:
            seeds = current_seeds
    requested = resolved["requested"]
    selected = (requested["budget_unit"], requested["max_iters"])
    budget_details = {"budgets": [{"budget_unit": unit, "max_iters": count}
                                   for unit, count in sorted(budgets)]}
    if budgets:
        if any(count < 1 for _, count in budgets):
            raise ExistingIntentValidationError("budget_invalid",
                "训练预算必须是正整数；请确认本次训练数量。", details=budget_details)
        if len(budgets) > 1:
            raise ExistingIntentValidationError("budget_conflict",
                "当前用户消息包含多个训练预算；请明确本次训练数量与steps/epochs单位。", details=budget_details)
        if budgets != {selected}:
            raise ExistingIntentValidationError("budget_mismatch",
                "提取的训练数量或steps/epochs单位与当前用户消息不一致；应按用户明确预算执行。",
                requires_user_input=False, details={**budget_details, "expected_budget": budget_details["budgets"][0]})
    else:
        default = resolved["default_budget"]
        if selected in excluded_budgets:
            raise ExistingIntentValidationError("budget_rejected",
                "用户已明确否定这个训练预算；请确认要执行的预算，不能回退使用被否定的配置值。")
        if (default.get("budget_unit"), default.get("max_iters")) != selected:
            raise ExistingIntentValidationError("budget_missing",
                "当前用户消息没有明确训练预算，配置也没有相同的显式预算；不能借用历史run预算。")
    if seeds:
        if seeds != {requested["seed"]}:
            raise ExistingIntentValidationError("seed_conflict" if len(seeds) > 1 else "seed_mismatch",
                "提取的seed与当前用户消息不一致。", requires_user_input=len(seeds) > 1,
                details={"seeds": sorted(seeds), **({"expected_seed": next(iter(seeds))} if len(seeds) == 1 else {})})
    elif requested["seed"] in excluded_seeds:
        raise ExistingIntentValidationError("seed_rejected",
            "用户已明确否定这个seed；请确认本次种子，不能回退使用被否定的配置值。")
    elif resolved["default_seed"] is None and requested["seed"] is None:
        if not resolved.get("seed_generation_required") and not resolved.get("ordinary_coding"):
            raise ExistingIntentValidationError("seed_missing",
                "当前配置没有可确认种子，必须保留普通Coding或明确登记新任务种子策略。")
    elif (resolved["default_seed"] is None and resolved.get("seed_source") == "generated_for_run"
          and type(requested["seed"]) is int and 0 <= requested["seed"] <= 0xFFFFFFFF):
        pass  # Generated only by the new-task boundary, never by model extraction.
    elif requested["seed"] != resolved["default_seed"] or type(resolved["default_seed"]) is not int:
        raise ExistingIntentValidationError("seed_unconfirmed",
            "当前用户消息没有指定该seed，不能覆盖实际配置中的种子。")


def user_requests_additional_changes(user_texts: list[str]) -> bool:
    """Conservatively retain ordinary Coding for explicit unsupported changes.

    This does not infer a research goal or patch source. It prevents an obvious
    change instruction from disappearing when a model only extracts a budget.
    """
    subjects = r"(?:模型|代码|结构|层|抽头|参数|配置|学习率|优化器|损失|调度|LUT|kernel|learning.rate|optimizer|loss)"
    changes = r"(?:新增|增加|减少|替换|删除|改动|修改|调整|提高|降低|改为|改变|减半|翻倍|加倍|增大|缩小)"
    assignments = (r"(?<![A-Za-z0-9_])(?:lr_init|learning_rate|batch_size|optimizer|loss(?:\.type)?|"
                   r"outer_kernel|inner_kernel|mix_kernel|memory_kernel|kernel_size|dilation|groups|"
                   r"lut_n_spline|lut_rmax|lut_init|reg_coeff|weight_decay|step_size|if_scheduler)\s*[:=]")
    for raw in user_texts:
        # Negation scopes over an action clause, including its object and
        # assignments. Remove it before detecting either Chinese or English
        # changes; "不要增加模型代码" must not request a new Coding task.
        clauses = re.split(r"[，,。；;\n]|但是|但|不过|而是|\bbut\b|\bhowever\b", raw, flags=re.IGNORECASE)
        positive = []
        for clause in clauses:
            negative = re.search(r"(?:不要|不必|无需|不用|禁止|别|不)(?:再)?(?!只)(?:" + changes + r"|改)|"
                r"\b(?:do\s+not|don't|no\s+need\s+to|without)\s+(?:change|modify|replace|add|remove|halve|double|increase|decrease)\b", clause, re.IGNORECASE)
            positive.append(clause[:negative.start()] if negative else clause)
        text = '，'.join(positive)
        if re.search(assignments, text, re.IGNORECASE):
            return True
        if (re.search(changes + r".{0,18}" + subjects, text, re.IGNORECASE)
                or re.search(subjects + r".{0,18}" + changes, text, re.IGNORECASE)
                or re.search(r"\b(?:change|modify|replace|add|remove|halve|double|increase|decrease)\b.{0,30}(?:model|layer|kernel|optimizer|loss|learning.rate)", text, re.IGNORECASE)):
            return True
    return False


def existing_execution_stage_limit(args: dict[str, Any], user_texts: list[str]) -> bool:
    """Freeze an explicit output boundary before the run can start."""
    stop = False
    for text in user_texts:
        without_report = re.search(
            r"(?:不要|不需要|无需|不用|不|别)(?:再)?(?:生成|撰写|写|输出|制作)?(?:研究|实验)?报告|"
            r"报告.{0,6}(?:不要|不用|不)(?:生成|写)|\b(?:no\s+report|without\s+(?:a\s+)?report)\b",
            text, re.IGNORECASE)
        positive_stop = re.sub(r"(?:不要|不|别)(?:只)?停(?:止)?(?:在|于)(?:仿真|执行)?结果", "", text)
        at_results = re.search(r"停(?:止)?(?:在|于)(?:仿真|执行)?结果|\bstop\s+after\s+execution\b",
                               positive_stop, re.IGNORECASE)
        if without_report or at_results:
            stop = True
        elif re.search(r"(?:要|需要|继续)(?:生成|撰写|写|输出)?(?:研究|实验)?报告", text):
            stop = False
    declared = args.get("stop_after_execution")
    if declared is not None and declared is not stop:
        raise ValueError("停止阶段与用户明确的报告要求不一致；仅明确停在结果或不生成报告时设置true。")
    return stop


def existing_model_turn_reply(tool: str, result: dict[str, Any]) -> str | None:
    """Render an actual creation receipt, not an Agent or training success."""
    if (tool != "run.existing_model" or result.get("ok") is not True
            or result.get("entrypoint") != "coding" or not isinstance(result.get("run_id"), str)
            or not result["run_id"] or result.get("status") not in {"created", "preparation_started"}):
        return None
    operation = "已启动编码准备" if result["status"] == "preparation_started" else "已创建编码准备任务"
    boundary = "仿真完成后停在结果，不生成报告。" if result.get("stop_after_execution") is True else ""
    return (f'{operation}：{result["run_id"]}。已有模型直接从编码准备进入执行，不安排调研或实验设计。'
            "核对本次配置后，将按当前审核设置开始仿真。" + boundary)
