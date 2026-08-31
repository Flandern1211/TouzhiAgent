# 项目结构

本文档描述当前 v1 最小实现的 Python 项目结构和职责边界。

## 当前目录

```text
.
├── AGENTS.md
├── README.md
├── .gitignore
├── docs/
│   ├── requirements/
│   │   └── fund-agent-v1-requirements.md
│   ├── coding/
│   │   ├── PRD.md
│   │   ├── TSD.md
│   │   ├── DESIGN.md
│   │   ├── API.md
│   │   └── TSD/
│   │       └── 2026-08-27-fund-agent-v1-minimal-design.md
│   ├── project-structure.md
│   └── project-conventions.md
├── src/
│   └── fund_agent/
│       ├── __init__.py
│       ├── config/
│       ├── sources/
│       ├── funds/
│       ├── screening/
│       ├── portfolio/
│       ├── tracking/
│       │   ├── rules.py
│       │   ├── service.py
│       │   └── scheduler.py
│       ├── agent/
│       ├── alerts/
│       └── persistence/
│           ├── repository.py
│           └── mysql.py
└── tests/
    ├── unit/
    └── integration/
```

## 已确认范围对应的边界

| 目录 | 只负责的已确认范围 |
| --- | --- |
| `config/` | 本机服务、远程基础服务、数据源和跟踪频率的配置入口。 |
| `sources/` | 默认简单爬虫和可配置外部爬虫 API 的来源适配边界；保留来源、时间和状态。 |
| `funds/` | 基金代码、产品与 A/C/E 等份额类别的身份，以及手动候选集合。 |
| `screening/` | 输入基金集合的客观排序、个性化排序、分级和理由所需的边界。 |
| `portfolio/` | 当前持仓快照及组合收益、仓位、集中度和风险分析所需的边界。 |
| `tracking/` | 可配置频率的持续跟踪、异常规则触发和数据状态。 |
| `agent/` | 基于多来源证据的综合复核、解释和不确定性表达。 |
| `alerts/` | 系统内风险提醒的记录、状态和展示数据边界。 |
| `persistence/` | 内存仓库和服务器 MySQL 仓库；v1 表用于基金、持仓快照、证据和风险提醒。 |

## v1 暂不包含

以下内容仍没有在当前版本中实现：

- 具体基金行情、公告、新闻和社交媒体供应商的领域解析器；
- LLM 供应商调用和生成式 Agent；当前复核器是证据约束的确定性实现；
- 数据库迁移编排、账号系统和多用户隔离；
- 账户登录、外部账户导入、券商交易和消息推送。
