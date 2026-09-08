此次启动在第一次 write_stdin 调用时被自动审批审查终止。输入为公开符号 LUT 任务，代码工具和项目规则上下文均关闭；核对之后另启 040828 运行，其结果须单独报告。

原始 trace 记录 1 次模型请求、0 次模型响应、0 次工具执行。模型结果、实际 token 用量和费用未知，不能把 checkpoint 中的零计数当作已确认零消耗，不能算完整测试或成功。

本目录逐字节保存原始输入、checkpoint、events、facts、进度与 summary，SHA256 见 archive_manifest.json。原 checkpoint 的 running 状态保持原样，表示中断时留下的快照，不代表进程仍在运行。未修改原始 run 文件。
