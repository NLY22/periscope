# 测试组织

- `architecture/`：目录归属、兼容模块、安装入口和产物迁移。
- `collection/`：来源体系、原生采集和站点爬虫。
- 根目录既有测试：证据内核、日报、研究、Web/MCP 与早期兼容契约。

共享资源仍由根目录 `conftest.py` 与 `pytest_scratch.py` 管理。无需将每个测试写成兼容文件；原有整套测试命令会递归收集新目录。

```text
python -m pytest
python -m pytest tests/architecture tests/collection
```
