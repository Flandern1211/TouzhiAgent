# TouzhiAgent

面向中国公募基金的个人研究辅助和风险监测系统。

当前状态：v1 最小闭环已实现，支持本机运行和测试；外部数据源与远程 MySQL 需要通过环境变量配置。

## 已确认的 v1 能力

- 手动维护多只基金并进行客观、个性化筛选；
- 手动维护当前持仓快照并分析组合风险；
- 从公开行情、官方公告、财经/行业新闻和社交媒体持续跟踪；
- 由固定规则触发风险复核，Agent 结合证据解释；
- 在本机系统内显示风险提醒；
- 默认简单爬虫，并可配置外部爬虫 API；
- 本机运行，MySQL 等基础服务连接服务器。

详细范围见 [v1 需求规格](docs/requirements/fund-agent-v1-requirements.md)。

## 当前不包含

交易流水、账户自动导入、券商交易、自动调仓和外部消息推送尚未纳入 v1。

## 项目文档

- [项目结构](docs/project-structure.md)
- [项目约定](docs/project-conventions.md)
- [PRD 索引](docs/coding/PRD.md)
- [API 合同](docs/coding/API.md)

## 本机运行

```powershell
python -m pip install -e ".[dev]"
python -m fund_agent
```

浏览器访问 `http://127.0.0.1:8000`。未配置数据库时使用内存存储；配置远程 MySQL 后自动使用 MySQL 持久化。环境变量前缀为 `FUND_AGENT_`，详见 [API 合同](docs/coding/API.md) 和 [需求规格](docs/requirements/fund-agent-v1-requirements.md)。
