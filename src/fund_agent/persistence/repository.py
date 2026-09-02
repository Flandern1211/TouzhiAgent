from __future__ import annotations

from typing import Protocol

from fund_agent.domain.models import Evidence, FundShare, HoldingSnapshot, RiskAlert, AlertStatus


class Repository(Protocol):
    def save_fund(self, fund: FundShare) -> FundShare: ...
    def list_funds(self) -> list[FundShare]: ...
    def delete_fund(self, code: str) -> bool: ...
    def save_snapshot(self, snapshot: HoldingSnapshot) -> HoldingSnapshot: ...
    def latest_snapshots(self, subject: str | None = None) -> list[HoldingSnapshot]: ...
    def save_evidence(self, evidence: Evidence) -> Evidence: ...
    def list_evidence(self, subject: str | None = None) -> list[Evidence]: ...
    def save_alert(self, alert: RiskAlert) -> RiskAlert: ...
    def list_alerts(self, subject: str | None = None, status: AlertStatus | None = None) -> list[RiskAlert]: ...


class InMemoryRepository:
    def __init__(self):
        self._funds: dict[str, FundShare] = {}
        self._snapshots: list[HoldingSnapshot] = []
        self._evidence: list[Evidence] = []
        self._alerts: list[RiskAlert] = []
        self._next_id = 1

    def _id(self) -> str:
        value = str(self._next_id); self._next_id += 1; return value

    def save_fund(self, fund: FundShare) -> FundShare:
        self._funds[fund.identity_key] = fund
        return fund

    def list_funds(self) -> list[FundShare]:
        return list(self._funds.values())

    def delete_fund(self, code: str) -> bool:
        keys = [key for key, fund in self._funds.items() if fund.code == code or key == code]
        for key in keys:
            self._funds.pop(key, None)
        return bool(keys)

    def save_snapshot(self, snapshot: HoldingSnapshot) -> HoldingSnapshot:
        self._snapshots.append(snapshot)
        return snapshot

    def latest_snapshots(self, subject=None):
        selected = [s for s in self._snapshots if subject is None or s.fund.code == subject]
        latest = {}
        for item in selected:
            key = item.fund.identity_key
            if key not in latest or item.as_of > latest[key].as_of:
                latest[key] = item
        return list(latest.values())

    def save_evidence(self, evidence: Evidence) -> Evidence:
        if evidence.id is None: evidence = evidence.model_copy(update={"id": self._id()})
        self._evidence.append(evidence); return evidence
    def list_evidence(self, subject: str | None = None) -> list[Evidence]:
        return [e for e in self._evidence if subject is None or e.subject == subject]

    def save_alert(self, alert: RiskAlert) -> RiskAlert:
        if alert.id is None: alert = alert.model_copy(update={"id": self._id()})
        self._alerts.append(alert); return alert
    def list_alerts(self, subject: str | None = None, status: AlertStatus | None = None) -> list[RiskAlert]:
        return [a for a in self._alerts if (subject is None or a.subject == subject) and (status is None or a.status == status)]
