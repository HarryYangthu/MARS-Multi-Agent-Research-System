"""Project workspace: pinned base copy, candidate branches, scope checks, and real evaluation through the project's
declared commands (locally or on the remote GPU server). No LLM and no domain knowledge here: every number comes from
the project's own evaluation command running on its own data.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from mars import osutil
from mars.project import ROOT, ExecutionContext, fill, fingerprint, project_id

GIT_ID = ["-c", "user.name=MARS", "-c", "user.email=mars@localhost"]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()[:800]}")
    return result.stdout.strip()


@contextmanager
def running_job(info: dict[str, Any], jobs_dir: Path) -> Iterator[None]:
    """Record a job (evaluation or coding session) for api.active_jobs while it actually runs."""
    import uuid
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps({**info, "pid": os.getpid(), "started": time.time()}, ensure_ascii=False), encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def last_json(text: str) -> dict[str, Any] | None:
    lines = [line for line in text.splitlines() if line.strip().startswith("{")]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def worse_by(metric: dict[str, Any], candidate: float, baseline: float) -> float:
    """How much the candidate is worse than the baseline in the metric's own direction (negative = better)."""
    return candidate - baseline if metric["better"] == "lower" else baseline - candidate


@dataclass
class EvalJob:
    workdir: Path          # candidate checkout (or the base checkout for the baseline)
    dataset: str           # a dataset name declared by the project
    steps: int             # training/evaluation budget passed to the command; 0 = the project's full budget
    seed: int
    baseline: bool = False
    overrides: dict[str, Any] | None = None   # extra config for this job only (hyperparameter search)
    label: dict[str, Any] | None = None       # who asked (run_id, route, iteration, stage); shown by active_jobs only


class Workspace:
    def __init__(self, context: ExecutionContext, environment: str | None = None) -> None:
        self.context = context
        self.scope = context.scope
        if environment not in (None, "local", "remote"):
            raise ValueError("执行环境必须是 local（本地）或 remote（远程 GPU）")
        if environment and environment != context.environment:
            context = ExecutionContext(context.scope, context.binding, environment, context.moved_from)
            self.context = context
        self.cfg = context.config()
        self.environment = "remote" if self.cfg["remote"].get("enabled") else "local"
        self.workspace = context.workspace
        if context.moved_from:
            self._repair_worktrees(self.workspace)
        self.jobs = self.scope.workspace / "jobs"
        self.base = self.workspace / "base"
        self.candidates = self.workspace / "candidates"
        self.evals = self.workspace / "evals"
        self.pack = Path(self.cfg["pack"])
        self.metrics = self.cfg["metrics"]
        self._slots = threading.Semaphore(int(self.cfg["evaluate"]["max_parallel"]))
        self._remote_slots = threading.Semaphore(int(self.cfg["remote"].get("max_parallel") or 1))
        self._git_lock = threading.Lock()
        self._job_locks: dict[str, threading.Lock] = {}
        self._job_locks_guard = threading.Lock()
        self._verified_remote: set[tuple[str, str]] = set()

    # ---------- workspace ----------
    @staticmethod
    def _repair_worktrees(workspace: Path) -> None:
        """git records absolute worktree paths; after the project folder moved, re-link base and candidates."""
        base, candidates = workspace / "base", workspace / "candidates"
        if (base / ".git").is_dir():
            paths = [str(p) for p in sorted(candidates.iterdir()) if (p / ".git").is_file()] if candidates.is_dir() else []
            subprocess.run(["git", "-C", str(base), "worktree", "repair", *paths], capture_output=True)

    def ensure_base(self) -> str:
        """Pinned local copy of the bound commit (plus the project's setup patch); the source repository is never written."""
        repo = self.cfg["repo"]
        if not self.base.resolve().is_relative_to(self.workspace.resolve()):
            raise ValueError("项目基线路径链接到绑定工作区以外")
        self.context.validate()
        self.workspace.mkdir(parents=True, exist_ok=True)
        with (self.workspace / "base.lock").open("a+", encoding="utf-8") as handle:
            osutil.lock(handle, blocking=True)
            marker = self.workspace / "baseline.json"
            if not self.base.exists():
                with tempfile.TemporaryDirectory(dir=self.workspace) as folder:
                    base = Path(folder) / "repo"
                    base.mkdir()
                    git(base, "init", "-q")
                    git(base, "fetch", "-q", "--depth", "1", repo["source"], repo["commit"])
                    git(base, "checkout", "-q", "-b", "mars/base", repo["commit"])
                    if repo.get("setup_patch"):
                        patch = Path(repo["setup_patch"])
                        if patch.read_text(encoding="utf-8", errors="replace").startswith("From "):
                            git(base, "am", "-q", str(patch))
                        else:
                            git(base, "apply", str(patch))
                            git(base, "add", "-A")
                            git(base, "commit", "-q", "-m", "MARS: project setup patch")
                    commit = git(base, "rev-parse", "HEAD")
                    marker.write_text(json.dumps({"binding_id": self.context.binding["id"], "commit": commit}), encoding="utf-8")
                    (self.workspace / "binding.json").write_text(json.dumps(self.context.snapshot(), ensure_ascii=False), encoding="utf-8")
                    base.rename(self.base)
            if not marker.exists() or json.loads(marker.read_text(encoding="utf-8")) != {"binding_id": self.context.binding["id"], "commit": git(self.base, "rev-parse", "mars/base")}:
                raise ValueError("项目基线副本与绑定不一致，拒绝套用其他来源或被修改的基线")
            return git(self.base, "rev-parse", "mars/base")

    def _candidate_only(self) -> list[str]:
        return [f":!{pattern}" for pattern in self.cfg["editable_files"]]

    def baseline_commit(self, ref: str = "mars/base", cwd: Path | None = None) -> str:
        """Last commit that touched anything other than the candidate (editable) files."""
        return git(cwd or self.base, "log", "-1", "--format=%H", ref, "--", ".", *self._candidate_only())

    def create_candidate(self, name: str, parent: str = "mars/base") -> Path:
        path = self.candidates / name
        if not path.resolve().is_relative_to(self.workspace.resolve()):
            raise ValueError("候选路径越过项目绑定工作区")
        with self._git_lock:
            if path.exists():
                return path
            self.candidates.mkdir(parents=True, exist_ok=True)
            git(self.base, "worktree", "add", "-q", "-b", f"cand/{name}", str(path), parent)
        return path

    def remove_candidate(self, name: str) -> None:
        path = self.candidates / name
        with self._git_lock:
            if path.exists():
                git(self.base, "worktree", "remove", "--force", str(path))
            if subprocess.run(["git", "rev-parse", "--verify", "-q", f"cand/{name}"], cwd=self.base,
                              capture_output=True).returncode == 0:
                git(self.base, "branch", "-D", f"cand/{name}")

    def relink(self, workdir: Path) -> bool:
        """Keep a candidate a worktree of the shared repository: the agent can rewrite the `.git` pointer (seen twice: commits
        went to a private repository). Restores it; True when it had been changed."""
        pointer = workdir / ".git"
        registry = self.base / ".git/worktrees"
        admin = next((d for d in registry.iterdir() if (d / "gitdir").exists()
                      and Path((d / "gitdir").read_text(encoding="utf-8").strip()) == pointer), None) if registry.is_dir() else None
        if admin is None:  # not a worktree this kernel created (e.g. a standalone checkout): nothing to restore
            return False
        expected = f"gitdir: {admin}\n"
        if pointer.is_file() and pointer.read_text(encoding="utf-8") == expected:
            return False
        if pointer.is_dir():
            import shutil
            shutil.rmtree(pointer)
        pointer.write_text(expected, encoding="utf-8")
        return True

    def commit(self, workdir: Path, message: str) -> str:
        self.relink(workdir)  # commits must land in the shared repository the evaluations and diffs read
        git(workdir, "add", "-A")
        if git(workdir, "status", "--porcelain"):
            git(workdir, "commit", "-q", "-m", message)
        self.sync_base(workdir)
        return git(workdir, "rev-parse", "HEAD")

    def sync_base(self, workdir: Path) -> None:
        """Bring the current mars/base into a candidate branch created before it changed.
        On conflicts the candidate's own files win (-X ours), so only infrastructure files are updated."""
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", "mars/base", "HEAD"], cwd=workdir).returncode == 0
        if not ancestor:
            git(workdir, "merge", "-q", "--no-edit", "-X", "ours", "-m", "MARS: sync from mars/base", "mars/base")

    def changed_files(self, workdir: Path, against: str = "mars/base") -> list[str]:
        committed = git(workdir, "diff", "--name-only", against, "HEAD").splitlines()
        modified = git(workdir, "diff", "--name-only", "HEAD").splitlines()
        untracked = git(workdir, "ls-files", "--others", "--exclude-standard").splitlines()
        return sorted({p for p in committed + modified + untracked if p})

    def editable(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.cfg["editable_files"])

    def check_scope(self, workdir: Path) -> list[str]:
        """Repository rules; the project's own checks (cfg["checks"]) add domain rules. Empty = may be evaluated."""
        violations = []
        if self.relink(workdir):
            violations.append("改写了 .git 指针（指向了别的仓库），已恢复：候选必须留在共享仓库里，不能自建或切换 git 仓库")
        violations += [f"修改了不允许的文件：{path}" for path in self.changed_files(workdir) if not self.editable(path)]
        return violations

    def validate_overrides(self, overrides: dict[str, Any] | None, baseline: bool = False) -> dict[str, Any]:
        extra = dict(overrides or {})
        bad = sorted(set(extra) - set(self.cfg["editable_config_keys"]))
        if bad:
            raise ValueError(f"不允许的配置键：{bad}；允许：{self.cfg['editable_config_keys']}")
        return extra

    # ---------- project commands (checks, diagnostics) ----------
    def values(self, workdir: Path, **extra: Any) -> dict[str, Any]:
        return {"python": self.cfg["python"], "pack": str(self.pack), "project": str(self.scope.root), "workdir": str(workdir), **extra}

    def command(self, kind: str, name: str, workdir: Path, timeout: int = 1800, **extra: Any) -> dict[str, Any]:
        """Run a declared check or diagnostic locally in a checkout; its last JSON line is the result."""
        spec = self.cfg[kind].get(name)
        if spec is None:
            return {"ok": False, "error": f"项目没有声明 {kind}.{name}"}
        try:
            argv = fill(spec["command"], self.values(workdir, **extra))
            result = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, encoding="utf-8",
                                    timeout=int(spec.get("timeout_seconds", timeout)))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return {"ok": False, "error": f"{kind}.{name} 无法执行：{type(error).__name__}: {error}"[:1500]}
        value = last_json(result.stdout)
        if value is None:
            return {"ok": False, "error": f"{kind}.{name} 没有输出 JSON：{result.stderr.strip()[-1500:]}"}
        return value

    def checks(self, workdir: Path, when: str) -> dict[str, dict[str, Any]]:
        """Declared checks for one moment: "implement" (each repair turn) or "measure" (before training). A check's
        `when` is one of them or a list; without it the check runs at both."""
        def moments(spec: dict[str, Any]) -> list[str]:
            value = spec.get("when") or ["implement", "measure"]
            return [value] if isinstance(value, str) else value
        return {name: self.command("checks", name, workdir) for name, spec in self.cfg["checks"].items() if when in moments(spec)}

    def baseline_facts(self) -> dict[str, Any]:
        """Measure-time checks on the base checkout, cached per baseline commit (Step1 reads them as facts)."""
        cache = self.workspace / "baseline_checks.json"
        identity = digest({"commit": self.baseline_commit(), "pack": self.context.binding["pack_hash"]})
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("identity") == identity:
                return data["checks"]
        result = self.checks(self.base, "measure")
        if all(r.get("ok", True) for r in result.values()):
            cache.write_text(json.dumps({"identity": identity, "checks": result}, ensure_ascii=False), encoding="utf-8")
        return result

    # ---------- evaluation ----------
    def evaluate(self, job: EvalJob) -> dict[str, Any]:
        if job.dataset not in self.cfg["datasets"]:
            raise ValueError(f"数据集 {job.dataset} 未在本项目声明；可选 {list(self.cfg['datasets'])}")
        if type(job.steps) is not int or job.steps < 0:
            raise ValueError("steps 必须是非负整数（0 表示完整预算）")
        self.context.validate()
        if not job.workdir.resolve().is_relative_to(self.workspace.resolve()):
            raise ValueError("评测目录不属于此项目绑定的工作区")
        dirty = git(job.workdir, "status", "--porcelain")
        if dirty:
            raise RuntimeError(f"候选代码未提交，不能评测：{dirty[:300]}")
        overrides = self.validate_overrides(job.overrides, job.baseline)
        # candidate-only files cannot change the baseline; keep its cache across candidate edits
        commit = self.baseline_commit("HEAD", job.workdir) if job.baseline else git(job.workdir, "rev-parse", "HEAD")
        identity = self.evaluation_identity(job, commit, overrides)
        key = digest(identity)
        output = self.evals / f"{'baseline' if job.baseline else 'cand'}-{key}" / f"d{job.dataset}-n{job.steps}-s{job.seed}"
        if not output.resolve().is_relative_to(self.workspace.resolve()):
            raise ValueError("评测输出路径链接到绑定工作区以外")
        with self._job_locks_guard:
            lock = self._job_locks.setdefault(str(output), threading.Lock())
        output.parent.mkdir(parents=True, exist_ok=True)
        with lock, (output.parent / f"{output.name}.lock").open("w", encoding="utf-8") as handle:
            osutil.lock(handle, blocking=True)  # also serializes the same job across processes
            return self._evaluate_locked(job, commit, output, overrides, key)

    def evaluation_identity(self, job: EvalJob, commit: str, overrides: dict[str, Any]) -> dict[str, Any]:
        remote = self.cfg["remote"]
        target = {"environment": self.environment}
        if self.environment == "remote":  # the remote copy is checked against data_hash before training (verify_remote_data)
            target.update(name=remote["name"], device=remote.get("device"), profile=digest({k: remote.get(k) for k in ("host", "port", "user", "root")}),
                          data=remote["data"][job.dataset])
        return {"commit": commit, "dataset": job.dataset, "steps": job.steps, "seed": job.seed, "baseline": job.baseline,
                "overrides": overrides, "data_sha256": self.context.binding["data_hashes"][job.dataset],
                "config": self.cfg["datasets"][job.dataset]["config"], "command": self.cfg["evaluate"]["command"],
                "pack": self.context.binding["pack_hash"], "python": self.cfg["python"], "target": target,
                "binding_id": self.context.binding["id"]}

    def verify_remote_data(self, dataset: str) -> None:
        """The remote data must have the bound content. Checked once per process and file, only when an evaluation is about
        to start (cached results stay readable while the server is offline)."""
        remote = self.cfg["remote"]
        path = remote["data"][dataset]
        if (remote["name"], path) in self._verified_remote:
            return
        local = Path(self.cfg["datasets"][dataset]["data"])
        if local.is_dir():  # directories: content was fingerprinted locally; the remote copy is trusted by path
            self._verified_remote.add((remote["name"], path))
            return
        command = "sha256sum -- " + shlex.quote(path)
        measured = subprocess.run([*self._ssh(remote), "bash", "-lc", shlex.quote(command)], capture_output=True, text=True, encoding="utf-8", timeout=120)
        remote_hash = measured.stdout.split()[0] if measured.returncode == 0 and measured.stdout.strip() else None
        if not remote_hash or not re.fullmatch(r"[a-f0-9]{64}", remote_hash):
            raise ValueError("无法核验远程数据哈希，未启动评测；请检查连接和 remote.data_path")
        if remote_hash != self.context.binding["data_hashes"][dataset]:
            raise ValueError("远程数据与项目绑定的本地数据 SHA-256 不一致")
        self._verified_remote.add((remote["name"], path))

    def _cache_matches(self, result: dict[str, Any], output: Path, identity: str) -> bool:
        tail = output.relative_to(self.workspace).parts  # absolute run_dir changes when the folder moves
        return (result.get("ok") is True and result.get("cache_identity") == identity
                and result.get("project_id") == project_id(self.scope)
                and result.get("binding_id") == self.context.binding["id"]
                and Path(str(result.get("run_dir", ""))).parts[-len(tail):] == tail)

    def _evaluate_locked(self, job: EvalJob, commit: str, output: Path, overrides: dict[str, Any], cache_identity: str) -> dict[str, Any]:
        cached = output / "mars_result.json"
        if cached.exists():
            result = json.loads(cached.read_text(encoding="utf-8"))
            if self._cache_matches(result, output, cache_identity):
                return {**result, "run_dir": str(output), "project_root": str(self.scope.root)}  # the folder may have moved
            output.rename(output.with_name(output.name + ".stale-" + str(time.time_ns())))
        elif output.exists():  # left by an interrupted or failed attempt: the command starts from an empty place
            output.rename(output.with_name(output.name + ".stale-" + str(time.time_ns())))
        remote = self.cfg["remote"]
        if remote.get("enabled"):
            self.verify_remote_data(job.dataset)
        output.parent.mkdir(parents=True, exist_ok=True)  # the command creates {output} itself
        overrides_file = output.parent / f"{output.name}.overrides.json"
        overrides_file.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
        target = f"{remote.get('name', 'remote')}/{remote.get('device', '')}" if remote.get("enabled") else "local"
        info = {"kind": "evaluate", **(job.label or {}), "dataset": job.dataset, "steps": job.steps, "seed": job.seed,
                "baseline": job.baseline, "target": target, "output": str(output), "project_root": str(self.scope.root),
                "binding_file": str(self.workspace / "binding.json"),
                "remote_run": f"{remote.get('root')}/runs/{output.parent.name}/{output.name}" if remote.get("enabled") else None}
        started = time.monotonic()
        timeout = int(self.cfg["evaluate"]["timeout_seconds"])
        try:
            if remote.get("enabled"):
                with self._remote_slots, running_job(info, self.jobs):
                    try:
                        completed = self._run_remote(job, commit, output, overrides, remote)
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError, OSError) as error:
                        detail = getattr(error, "stderr", "") or ""
                        completed = subprocess.CompletedProcess([], 1, stdout=json.dumps(
                            {"ok": False, "error": f"远程执行失败：{type(error).__name__}: {error} {str(detail)[-300:]}"}), stderr="")
            else:
                argv = fill(self.cfg["evaluate"]["command"], self.values(
                    job.workdir, dataset=job.dataset, data=self.cfg["datasets"][job.dataset]["data"],
                    config=self.cfg["datasets"][job.dataset]["config"], steps=job.steps, seed=job.seed, output=output,
                    baseline=int(job.baseline), overrides=overrides_file))
                with self._slots, running_job(info, self.jobs):
                    completed = subprocess.run(argv, cwd=job.workdir, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
            raw = last_json(completed.stdout) or {"ok": False, "error": f"评测命令没有输出 JSON：{completed.stderr.strip()[-1500:]}"}
        except subprocess.TimeoutExpired:
            raw = {"ok": False, "error": f"评测超过 {timeout} 秒被终止"}
        except (OSError, ValueError) as error:
            raw = {"ok": False, "error": f"评测命令无法执行：{type(error).__name__}: {error}"}
        result = self._summarize(job, commit, output, raw)
        result.update(cache_identity=cache_identity, project_root=str(self.scope.root), project_id=project_id(self.scope, create=True),
                      binding_id=self.context.binding["id"], executed_on=target, elapsed_seconds=round(time.monotonic() - started, 1))
        if result["ok"]:
            self.context.validate()
            after = self.baseline_commit("HEAD", job.workdir) if job.baseline else git(job.workdir, "rev-parse", "HEAD")
            if after != commit or git(job.workdir, "status", "--porcelain"):
                raise ValueError("评测期间源码发生变化，未缓存此次结果")
            output.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result

    def _summarize(self, job: EvalJob, commit: str, output: Path, raw: dict[str, Any]) -> dict[str, Any]:
        base = {"dataset": job.dataset, "steps": job.steps, "seed": job.seed, "baseline": job.baseline,
                "commit": commit, "run_dir": str(output)}
        if not raw.get("ok"):
            return {**base, "ok": False, "error": str(raw.get("error", "评测命令报告失败"))[:2000]}
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
        missing = [name for name in self.metrics["fields"] if not isinstance(metrics.get(name), (int, float))]
        if self.metrics["primary"] in missing:
            return {**base, "ok": False, "error": f"评测输出缺少主指标 {self.metrics['primary']}"}
        return {**base, "ok": True, "metrics": metrics, "missing_metrics": missing,
                "curve": raw.get("curve") if isinstance(raw.get("curve"), list) else [],
                "info": raw.get("info") if isinstance(raw.get("info"), dict) else {},
                "plots": sorted(p.name for p in output.glob("*.png")) if output.is_dir() else []}

    def evaluate_many(self, jobs: list[EvalJob]) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
            return list(pool.map(self.evaluate, jobs))

    # ---------- remote GPU execution ----------
    def _ssh(self, remote: dict[str, Any]) -> list[str]:
        key_path = Path(remote["key"]).expanduser()
        key = str((key_path if key_path.is_absolute() else ROOT / key_path).resolve())
        return ["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30",
                "-p", str(remote["port"]), f"{remote['user']}@{remote['host']}"]

    def _run_remote(self, job: EvalJob, commit: str, output: Path, overrides: dict[str, Any], remote: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        """Push the exact commit and the project pack, run the evaluation command on the GPU host in a detached worktree,
        fetch the run directory back."""
        root, name = remote["root"], output.parent.name + "/" + output.name
        ssh = shlex.join(self._ssh(remote)[:-1])
        env = {**os.environ, "GIT_SSH_COMMAND": ssh}
        url = f"ssh://{remote['user']}@{remote['host']}:{remote['port']}{root}/repo.git"
        bootstrap = f"mkdir -p {shlex.quote(root)}; test -d {shlex.quote(root + '/repo.git')} || git init --bare -q {shlex.quote(root + '/repo.git')}"
        subprocess.run([*self._ssh(remote), "bash", "-lc", shlex.quote(bootstrap)], check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
        with self._git_lock:
            subprocess.run(["git", "push", "-q", "-f", url, f"{commit}:refs/mars/{commit}"], cwd=job.workdir,
                           env=env, check=True, capture_output=True, text=True, encoding="utf-8", timeout=600)
        pack = f"{root}/pack-{self.context.binding['pack_hash'][:12] if self.context.binding['pack_hash'] else 'none'}"
        if self.pack.is_dir():
            with tempfile.TemporaryFile() as archive:
                with tarfile.open(fileobj=archive, mode="w") as tar:
                    tar.add(self.pack, arcname=".", filter=lambda t: None if "__pycache__" in t.name else t)
                archive.seek(0)
                subprocess.run([*self._ssh(remote), "bash", "-lc", shlex.quote(f"mkdir -p {shlex.quote(pack)} && tar -C {shlex.quote(pack)} -xf -")],
                               stdin=archive, check=True, capture_output=True, timeout=300)
        run = f"{root}/runs/{name}"
        argv = fill(self.cfg["evaluate"]["command"], {
            "python": "python3", "pack": pack, "project": root, "workdir": f"{root}/wt/{commit}", "dataset": job.dataset,
            "data": remote["data"][job.dataset], "config": self.cfg["datasets"][job.dataset]["config"], "steps": job.steps,
            "seed": job.seed, "output": run, "baseline": int(job.baseline), "overrides": f"{run}.overrides.json"})
        # reached only without a valid local result, so a remote run dir left by an interrupted attempt is stale
        script = (f"set -e; cd {shlex.quote(root)}; [ -d wt/{commit} ] || git -C repo.git worktree add -q --detach ../wt/{commit} {commit}; "
                  f"mkdir -p runs/{output.parent.name}; rm -rf runs/{name}; cat > runs/{name}.overrides.json; "
                  f"cd wt/{commit}; {shlex.join(argv)}")
        completed = subprocess.run([*self._ssh(remote), "bash", "-lc", shlex.quote(script)], input=json.dumps(overrides),
                                   capture_output=True, text=True, encoding="utf-8", timeout=int(self.cfg["evaluate"]["timeout_seconds"]))
        output.mkdir(parents=True, exist_ok=True)
        # ssh + tar stream instead of rsync (absent on Windows); unpacked with the "data" filter (no links/absolute paths)
        with subprocess.Popen([*self._ssh(remote), "tar", "-C", run, "-cf", "-", "."],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as fetched:
            try:
                with tarfile.open(fileobj=fetched.stdout, mode="r|") as archive:
                    archive.extractall(output, filter="data")
                error = ""
            except tarfile.TarError as exc:
                error = f"{exc} {fetched.stderr.read().decode(errors='replace').strip()[-500:]}"
        if (error or fetched.returncode) and completed.returncode == 0:
            raise RuntimeError(f"取回远程结果失败：{error or fetched.returncode}")
        return completed

    # ---------- knowledge sources ----------
    def knowledge_files(self) -> list[tuple[Path, str]]:
        folder = self.workspace / "knowledge"
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for doc in self.context.binding["documents"]:
            path = folder / (digest(doc) + ".md")
            if not path.exists():
                path.write_text(doc["text"], encoding="utf-8")
            files.append((path, doc["kind"]))
        return files + [(self.base / name, "baseline_code") for name in self.cfg["repo"].get("context_files") or []
                        if (self.base / name).is_file()]

    def baseline_code(self, limit: int = 60_000) -> str:
        """The repository files the project marks as context (repo.context_files), for the models to read."""
        parts = [f"### {name}\n{(self.base / name).read_text(encoding='utf-8', errors='replace')}"
                 for name in self.cfg["repo"].get("context_files") or [] if (self.base / name).is_file()]
        return "\n\n".join(parts)[:limit] or "（项目未在 repo.context_files 中声明需要阅读的基线代码文件）"
