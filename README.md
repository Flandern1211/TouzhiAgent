# TouzhiAgent

面向中国公募基金的个人研究辅助与风险监测 Agent。

TouzhiAgent 支持手动维护基金候选和当前持仓，结合净值、公告、财经新闻、行业新闻与网络舆情，帮助用户比较基金、分析组合风险并持续发现需要关注的变化。

> 仅用于研究和决策辅助，不构成投资建议。历史表现不代表未来表现。

## 核心能力

- 多只基金的客观排序与个性化筛选；
- 支持 A/C/E 等基金份额类别识别；
- 当前持仓快照、收益、仓位、集中度和回撤分析；
- 多来源信息跟踪与证据留存；
- 固定规则发现异常，复核层解释风险；
- 本机 Web 界面和系统内风险提醒；
- 默认公开 HTTP 抓取器，可配置外部爬虫 API；
- 可选连接远程 MySQL 持久化数据。

## 快速开始

要求 Python `3.11+`：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m fund_agent
```

打开 `http://127.0.0.1:8000`。未配置 MySQL 时使用内存存储；配置项统一使用 `FUND_AGENT_` 前缀。

## 当前版本

当前为 v1 最小实现，重点覆盖：

- 手动基金候选管理；
- 多基金筛选；
- 当前持仓组合分析；
- 定时跟踪和系统内提醒。

当前不包含交易执行、账户自动导入、逐笔交易流水、外部消息推送、登录、多用户权限和公开部署。生成式 LLM 与具体新闻/公告供应商解析器将在后续需求确认后接入。

## 文档

- [v1 需求规格](docs/requirements/fund-agent-v1-requirements.md)
- [API 合同](docs/coding/API.md)
- [最小实现技术设计](docs/coding/TSD/2026-08-27-fund-agent-v1-minimal-design.md)
- [项目结构](docs/project-structure.md)
- [项目约定](docs/project-conventions.md)

## 开发验证

```powershell
python -m pytest -q
python -m compileall -q src tests
```
