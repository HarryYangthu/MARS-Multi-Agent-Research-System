# 项目 API 签名

API_VERSION = 3。所有接口都在某个项目文件夹内工作：`project_root` 省略时使用环境变量 `MARS_DEFAULT_PROJECT`，两者都没有时报错（不再有项目以外的默认工作区）。项目声明见 [项目接口](项目接口.md)。

```python
inspect_project(project_root: 'str | Path') -> 'dict[str, Any]'
start_run(task_path: 'str | Path', run_id: 'str' = '', auto_approve: 'bool' = False, environment: 'str | None' = None, *, project_root: 'str | Path | None' = None) -> 'str'
start_refine(task_path: 'str | Path', proposal: 'str | Path', run_id: 'str' = '', auto_approve: 'bool' = False, environment: 'str | None' = None, *, project_root: 'str | Path | None' = None) -> 'str'
implement(task_path: 'str | Path', spec: 'str | Path', run_id: 'str' = '', parent: 'str' = 'mars/base', environment: 'str | None' = None, *, project_root: 'str | Path | None' = None) -> 'str'
implementation(run_id: 'str', *, project_root: 'str | Path | None' = None) -> 'dict[str, Any] | None'
resume(run_id: 'str', approve: 'list[str] | None' = None, *, project_root: 'str | Path | None' = None) -> 'None'
list_runs(*, project_root: 'str | Path | None' = None) -> 'list[dict[str, Any]]'
events(run_id: 'str', since: 'int' = 0, *, project_root: 'str | Path | None' = None) -> 'list[dict[str, Any]]'
status(run_id: 'str', *, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
stop(run_id: 'str', wait_seconds: 'float' = 30, *, project_root: 'str | Path | None' = None) -> 'bool'
stop_route(run_id: 'str', route_id: 'str', reason: 'str' = '', *, project_root: 'str | Path | None' = None) -> 'None'
add_route_note(run_id: 'str', route: 'str', text: 'str', *, project_root: 'str | Path | None' = None) -> 'None'
route_summaries(run_id: 'str', *, project_root: 'str | Path | None' = None) -> 'list[dict[str, Any]]'
evaluate(candidate_dir: 'str | Path', dataset: 'str', steps: 'int', seed: 'int' = 2026, baseline: 'bool' = False, overrides: 'dict[str, Any] | None' = None, details: 'bool' = False, environment: 'str | None' = None, *, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
execution_environments(*, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
baseline_dir(*, project_root: 'str | Path | None' = None) -> 'str'
search_space(*, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
steps_per_epoch(candidate_dir: 'str | Path | None' = None, overrides: 'dict[str, Any] | None' = None, *, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
iteration_diff(run_id: 'str', route: 'str', iteration: 'int', max_patch_bytes: 'int' = 200000, *, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
active_jobs(run_id: 'str | None' = None, remote_progress: 'bool' = False, *, project_root: 'str | Path | None' = None) -> 'list[dict[str, Any]]'
codingagent_summary(run_id: 'str', route: 'str', iteration: 'int', *, project_root: 'str | Path | None' = None) -> 'dict[str, Any]'
```
