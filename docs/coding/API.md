# API 设计索引

API 文档记录 v1 本机 Web 服务的稳定合同。接口只服务于本机研究辅助，不连接券商、不执行交易，也不发送外部通知。

## 服务

- `GET /`：返回本机浏览器页面。
- `GET /api/health`：返回 `{ "status": "ok" }`。

## 基金候选

- `POST /api/funds`：手动添加基金。请求至少包含六位基金代码，可选名称、产品类型和份额类别。返回 `201` 和标准化的产品/份额对象。
- `GET /api/funds`：列出当前候选基金。
- `PUT /api/funds/{code}`：更新一个候选基金；路径和请求体代码必须一致。
- `DELETE /api/funds/{code}`：删除候选基金，不删除历史证据、持仓或提醒；成功返回 `204`。

## 分析

- `POST /api/screening`：输入 `funds` 数组和可选 `preference`，返回按个性化分数排序的结果，同时包含客观分数、个性化分数、四级标签、分项指标、理由、警告和证据引用。
- `POST /api/holdings`：保存当前持仓快照；至少包含代码、金额或份额、累计投入和快照时间。
- `GET /api/portfolio?latest_values={...}`：返回当前组合的估值、收益、仓位、贡献、集中度、历史风险指标和警告。

## 跟踪

- `POST /api/tracking/run`：可选输入 `funds` 和手动 `evidence`；未提供基金时跟踪仓库中的候选和当前持仓。返回 subjects、证据、每个来源状态、规则命中、复核结果和新建提醒。
- `GET /api/alerts`：返回系统内提醒历史。提醒包含风险等级、触发时间、原因、摘要、不确定性和证据引用。

所有时间使用 ISO 8601。数据来源状态可能为 `available`、`estimated`、`stale`、`failed` 或 `conflicting`。错误输入返回 `422`，不存在的基金返回 `404`。

## 爬虫与来源配置

来源获取采用外部优先、内部兜底：只有配置 `FUND_AGENT_CRAWLER_ENDPOINT` 时才请求外部爬虫 API；外部未配置、返回失败/空内容/无效记录时，系统自动使用内部基础爬虫。外部调用可能产生供应商计费、配额或请求次数成本，因此未配置时不会调用，外部成功时不会再调用内部，只有外部结果不可用时才发生一次内部兜底。内部爬虫失败不会伪造证据，会在证据 `status=failed` 和 `metadata.failure_reason` 中保留原因；发生降级时同时记录 `metadata.external_failure_reason`。

内部基础爬虫支持公开 HTTP/HTTPS 请求、超时、有限重试、请求头、字符集、同白名单内重定向、HTML 标题/正文/链接提取、域名白名单、按域名限频和最大响应大小。它不会执行 JavaScript、登录、绕过验证码或访问控制；这类页面会明确标记 `javascript_required`、`login_required` 或 `access_restricted`。

可配置环境变量：

```powershell
$env:FUND_AGENT_CRAWLER_ENDPOINT = "https://crawler.example/fetch" # 可选，外部优先
$env:FUND_AGENT_CRAWLER_API_KEY = "..." # 可选，不会返回到前端
$env:FUND_AGENT_CRAWLER_ALLOWED_DOMAINS = "fund.example,news.example"
$env:FUND_AGENT_CRAWLER_TIMEOUT_SECONDS = "10"
$env:FUND_AGENT_CRAWLER_MAX_RETRIES = "2"
$env:FUND_AGENT_CRAWLER_MAX_RESPONSE_BYTES = "2000000"
$env:FUND_AGENT_CRAWLER_MIN_INTERVAL_SECONDS = "0.25"
$env:FUND_AGENT_CRAWLER_USER_AGENT = "TouzhiAgent/0.1"
$env:FUND_AGENT_CRAWLER_FOLLOW_REDIRECTS = "true"
$env:FUND_AGENT_CRAWLER_RESPECT_ROBOTS = "true"
```

`FUND_AGENT_MARKET_ENDPOINT`、`FUND_AGENT_OFFICIAL_ENDPOINT`、`FUND_AGENT_NEWS_ENDPOINT` 和 `FUND_AGENT_SENTIMENT_ENDPOINT` 分别指定各类公开来源 URL。若未配置后面三类端点，系统不会猜测或静默抓取未知网站。`GET /api/settings` 只返回非密钥运行配置；跟踪响应的 `source_statuses` 和证据 `metadata` 用于查看最终来源、失败原因和是否发生降级。
