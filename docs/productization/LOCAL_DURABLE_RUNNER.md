# 独立本地 CPU 作业 runner

此模块为 `backend/app/execution/local/` 的独立开发切片，尚未接入 Orchestrator、ToolRegistry 或 StateJournal。它不解除冻结研究合同的执行阻断，不表示 23 项任务预算已生效。

调用方提供完整 `LocalJobSpec`：run/attempt/job/experiment 身份、绝对 executable/cwd、结构化 argv、单作业时限、捕获输出字节上限、必需指标、配置、种子和 steps。`LocalRunner(run_root).submit(spec)` 持久化后启动独立 supervisor；同 job ID 相同内容幂等，不同内容拒绝。返回值区分 queued/running/终态以及实际 owner 租约，不能把提交当完成。

复用 `local_command_request.v1`、`local_command_result.v1`、`local_command_receipt.v1`。命令从 `MARS_JOB_REQUEST` 读取输入并向 `MARS_RESULT_PATH` 写实际测量，声明真实 measurement evidence 文件；身份、有限指标、必需指标和证据哈希必须通过既有解析器。退出码 0 或日志不是成功测量。

supervisor 脱离启动者会话，独立持有从提交起的绝对截止和单调时钟余量，管理自己启动的命令进程组。`stop(job_id)` 只写停止意图，supervisor 取消它实际持有的组；API 不根据历史 PID 发信号。stdout/stderr 合计超过 max_output_bytes 时停止，最多保存该字节数。此上限不是工作目录磁盘配额；result JSON 另受既有 2 MiB 解析上限约束。

提交落盘到子进程启动之间若中断，不自动再次启动。supervisor 自身失去租约而没有回执时状态为 unknown；缺失/损坏控制文件拒绝读取，不凭推测恢复成功。此版本的独立 deadline 保障针对后端/启动者死亡，不能声称 supervisor 自身被 SIGKILL 后还能管理其子进程。后续统一账本必须对未知结果保留预留额度，不能退款后重复运行。

当前明确限制为 POSIX 本地可信 CPU 命令。设置 CUDA/HIP/ROCR 可见设备为空只是 CPU 配置，不是隔离恶意程序的 OS GPU/文件系统权限。命令及 argv 由可信调用方提供；尚未接合同命令授权、候选快照、项目保护范围或全任务并发/累计进程时间账本。环境过滤使用现有敏感变量过滤器。命令回执的 os_isolated 始终为 false。

## 本次真实验收

2026-09-28 macOS/POSIX：`test_local_durable_runner.py` 最终 **16 passed / 8.67 秒**，涵盖真实数值测量、相同身份幂等、两个独立提交进程争用、重复 supervisor、启动后停止、真正 SIGKILL 启动后端后独立截止、子孙进程组停止、日志输出上限、非零退出/无结果/错误身份、路径符号链接、未知结果不重启、截止记录篡改。测试不调用模型、没有 provider/tool/service 成功替身。

保留中间 attempt：首轮 **10 passed / 1 failed**，失败为测试自身嵌套 Python 字符串的换行转义错误，实际 stderr 显示 SyntaxError，子孙进程从未启动；修正测试后 **11 passed / 7.73 秒**，再增加并发/路径/篡改用例得到上述 16 项结果。首次严格 mypy 报一处状态 Literal 收窄错误，修正后新包与测试共 **4 个文件 strict mypy 通过**。

既有本地 command/process runtime/process adapter 定向回归 **13 passed / 2 skipped / 1.31 秒**；两项跳过是原 `test_process_runtime.py` 仅 Linux `/proc` 的进程树断言，不作为 runner 成功证据。没有执行全量回归或宣称 Windows/SSH 验收。

集成复核增加结果中心的只读解析：同一 request/result/receipt 协议，加验 durable submission/attempt/job/deadline 身份与指纹；不启动 worker、不修复文件。独立审查真实发现两项问题并保留复现：控制元数据可充当测量 evidence，失败回执仅改 status 可冒充 completed。已统一禁止控制文件作为测量证据，completed 状态重新核对退出码、错误、request/result 哈希及实际 measurement/curve/evidence；两个原复现均已独立确认拒绝。

最终集成定向回归 **97 passed / 16.830 秒**（runner、结果中心、旧本地命令和精确清单），6 个源文件 strict mypy 通过。前一条命令误写不存在的测试文件，退出 4，未计为通过；改正路径后完整执行。此次不是全仓回归，预算核心与 macOS 包仍单独开发。

当前停止直接终止所属命令组，还未提供可配置的 checkpoint/协作退出宽限；不能据此声称计划中的完整暂停/停止恢复机制已验收。
