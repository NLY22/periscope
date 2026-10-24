# 兼容采集入口

此目录保留原有脚本和导入路径。正式实现按职责放在：

- `periscope/collection/transport/`：请求、分页与响应解析。
- `periscope/collection/strategies/`：广泛、聚焦、批量和浏览器采集调度。
- `periscope/collection/crawling/`：站点遍历、网页/PDF 提取与解析子进程。
- `periscope/collection/platforms/`：各平台专用适配。
- `periscope/operations/`：导入、正文回填等运维。

新增代码请使用正式路径；旧脚本继续转交同一份实现。
