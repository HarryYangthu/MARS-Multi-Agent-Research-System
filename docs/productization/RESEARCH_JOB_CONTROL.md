# 合同作业逐项停止与核对

`bridge.research_job_control.control_owned_jobs(service, action="stop" | "reconcile", policy=...)` 是可信 owner 的显式内部接口，枚举同一 `run_state.sqlite3` 中已经绑定的作业。它不提交或重试作业、不调用模型、不改 RunGraph 生命周期，也不依据目录名或保存的 PID 猜测进程归属。

每个合法 SQL job_id 分别核验既有 `ResearchJobService` 身份和回执，并获取该服务与 LocalRunner 使用的同一个可重入锁。`configs/research_jobs.yaml` 的 `research_job_control.lock_timeout_seconds` 默认 1 秒；一个作业锁忙、回执损坏或适配器异常会留下安全的 `job_control_unconfirmed`，随后继续其他作业。普通异常在逐项边界隔离，进程中断仍传播。非法目录行不用于路径访问，也不回显其内容，只增加 `unverified_catalog_rows`。整个 SQL 身份或目录不可读时明确失败，不自行扫描磁盘认领作业。

`stop` 沿用已有合作停止协议，不能把已写停止请求当作进程已经结束。`reconcile` 依据真实 supervisor 回执结算；证据未知时保留原预留，不退款，不创建新任务。缺失外部冻结声明不阻止依据原 SQL 身份停止所属进程；源快照损坏仍不能接受实验结果。

结果包含本次观察的作业、逐项失败、无法核验的目录行数、前后目录一致性，以及是否观察到停止派发的状态。`all_observed_jobs_terminal` 只有全部观察到的合法作业都有真实终态、owner 已退出且目录完整稳定时成立；空目录可以成立。`run_stop_confirmed` 始终为 false：本函数没有持有完整编排器的派发权限，也没有证明所有阶段或外部资源停止。后续 owner 必须先建立派发屏障，并分别核对在途模型、工具和计算作业。

锁等待有界不等于总调用时限；SQLite、文件系统访问和每个作业的同步核验仍有各自成本，串行多作业也会累积等待。此切片不能用作产品“2 秒停止受理 / 5 秒禁止新工作 / 30 秒停止”门槛通过证据。当前仅适用于既有可信本地 CPU adapter，没有 OS 沙箱、SSH 或 GPU 接线。

## 验证与独立发现

新增 15 个真实临时文件、SQLite 和独立 Python worker 检查；与合同作业服务、本地持久 runner、StateJournal 联合 **100 项通过，23.39 秒**，strict mypy 4 文件通过。覆盖双作业实际停止、真实外部进程持锁、坏 binding、非法 SQL job_id、五种坏 submission JSON、未知预算保留、无关 runner 作业不受影响，以及观察到 cancelled 也不冒报整 run 停止。

独立审查先发现两个失败：合法 JSON 数组导致 AttributeError 中断整个循环；单个非法目录 ID 导致健康作业也无法停止。分别补 submission 对象验证、逐项异常隔离及坏行计数，保留原始失败日志并复跑。另以真实线程验证同线程重入、另一线程有限等待和释放后可再次获取。最早父任务测试有一项 fixture 漏填必需 steps，修正后保留日志，不算产品通过。

原 `path_lock(path)` 的无限等待默认行为保留，只有显式传入 timeout 的新调用使用有界等待。精确源文件与日志指纹见 `evidence/research-owner-primitives.json`；完整产品执行入口仍未解除阻断。
