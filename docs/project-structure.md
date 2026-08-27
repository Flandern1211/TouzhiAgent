# 项目结构

本文档描述当前已确认范围对应的最小 Python 骨架。目录只表达职责边界，不代表相关业务已经实现，也不预先决定具体框架。

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
│   │   └── PRD.md
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
│       ├── agent/
│       ├── alerts/
│       └── persistence/
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
| `persistence/` | 连接服务器 MySQL 等基础服务的持久化边界；具体表结构尚未确定。 |

## 尚未初始化的内容

以下内容没有在当前骨架中创建，因为需求或实现方式尚未确认：

- Web、桌面或命令行的具体界面实现；
- 具体 Python Web 框架、Agent 框架、任务队列或缓存服务；
- 数据库表、迁移脚本和部署编排；
- 具体行情、公告、新闻和社交媒体供应商配置；
- 评分权重、风险阈值、提醒去重/升级规则；
- 账户登录、外部账户导入、券商交易和消息推送。
