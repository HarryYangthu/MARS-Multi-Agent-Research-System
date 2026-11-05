# mars-app 开发约束

MARS 外围：Web 看板、后端服务、CLI 包装，以及不需要完整 RSI 的提效场景。研究引擎在 `../mars-core`，由另一条开发线维护。

## 硬约束
1. 只依赖内核契约（整体计划 §4）：控制与状态只经 `mars.api`（`server/mars_app/core.py` 是唯一入口，检查 `API_VERSION`）；详细视图按 §4.3 只读 `runs/<id>/` 产物。不读 `checkpoint.sqlite`，不 import `mars.api` 以外的内核模块。
2. 不修改 `mars-core/` 下的任何文件。本应用生成的研究约定放在 `data/tasks/`。内核缺能力时向 MARS-core 会话提需求，不在外围绕过。
3. 只允许真实执行：缺内核、缺产物或命令失败时如实报错，不伪造运行、进度或指标。测试只测纯函数和真实临时文件。
4. 人工确认：`api.status()` 报 `awaiting_approval` 时展示 `pending_approval` 与方案详情，用户选定后调用 `api.resume(run_id, approve=[...])`；所选 id 必须在本轮排序里。向导里可选择关闭人工确认（`auto_approve=True`）。
5. 内核产物字段只增不删，读取时对缺失字段容错，不因为新字段报错。

## 目录
- `server/mars_app/core.py`：加载 `mars.api`、版本检查
- `server/mars_app/artifacts.py`：读取并汇总运行产物、人工确认候选（纯函数）
- `server/mars_app/launcher.py`：校验并写出研究约定、经 `mars.api` 启动
- `server/mars_app/export/`：Word / PPT / Excel 导出（生成器取自 MARS-Product reporting；`context.py` 只整理产物，不调用模型、不做新判定；文件写到 `data/exports/`，从不写进内核运行目录）
- `server/mars_app/models_config.py`：模型连接配置（只读写 `MARS_MODELS_CONFIG` 指向的文件及其 env_files，从不写 `mars-core/configs`；Key 不出接口；测试连接调用 `mars-deploy/checks/check_models.py`）
- `server/mars_app/verify.py`：仿真验证（implement → 候选与基线 evaluate 对比）
- `server/mars_app/hpo.py`：超参寻优（Optuna 研究存于 `data/hpo/<id>/`，每个试验调用 `api.evaluate(overrides=...)`；GPU 与进化路线共用，全局并发上限 `MAX_PARALLEL`）
- `server/mars_app/main.py`：FastAPI 路由、SSE 事件流、托管前端构建
- `web/`：Vite + React + TypeScript 前端
- `tests/`：单元测试
