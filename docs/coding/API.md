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
