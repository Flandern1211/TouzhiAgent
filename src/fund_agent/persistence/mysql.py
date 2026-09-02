from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from fund_agent.domain.models import AlertStatus, Evidence, EvidenceStatus, FundShare, HoldingSnapshot, RiskAlert, RiskLevel, SourceType

try:
    import pymysql
except ImportError:  # pragma: no cover - optional at runtime
    pymysql = None


class MySqlRepository:
    """Small parameterized MySQL boundary; credentials are accepted only at runtime."""

    def __init__(self, connection: Any | None = None, *, connect: Callable[[], Any] | None = None, **connection_kwargs: Any):
        self._connection = connection
        self._connect = connect
        self._connection_kwargs = connection_kwargs

    def _get_connection(self):
        if self._connection is not None:
            return self._connection
        if self._connect is not None:
            return self._connect()
        if pymysql is not None:
            return pymysql.connect(**self._connection_kwargs)
        raise RuntimeError("PyMySQL is required for MySqlRepository")

    def _close_if_owned(self, connection: Any) -> None:
        # Injected connections are test doubles or externally managed pools. Runtime
        # connections created by this repository are short-lived per operation.
        if self._connection is not None:
            return
        close = getattr(connection, "close", None)
        if close:
            close()

    def initialize_schema(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS funds (identity_key VARCHAR(16) PRIMARY KEY, code VARCHAR(6) NOT NULL, product_id VARCHAR(255), name VARCHAR(255), category VARCHAR(100), share_class VARCHAR(1), INDEX idx_funds_code (code))""",
            """CREATE TABLE IF NOT EXISTS holding_snapshots (id BIGINT AUTO_INCREMENT PRIMARY KEY, fund_identity_key VARCHAR(16) NOT NULL, fund_code VARCHAR(6) NOT NULL, amount DOUBLE, units DOUBLE, invested DOUBLE NOT NULL, as_of DATETIME(6) NOT NULL, manual_value DOUBLE, INDEX idx_holding_identity (fund_identity_key, as_of))""",
            """CREATE TABLE IF NOT EXISTS evidence (id VARCHAR(64) PRIMARY KEY, source_type VARCHAR(32) NOT NULL, subject VARCHAR(255) NOT NULL, collected_at DATETIME(6) NOT NULL, effective_at DATETIME(6), url TEXT, content LONGTEXT, confidence DOUBLE NOT NULL, status VARCHAR(32) NOT NULL, metadata JSON)""",
            """CREATE TABLE IF NOT EXISTS risk_alerts (id VARCHAR(64) PRIMARY KEY, subject VARCHAR(255) NOT NULL, level VARCHAR(32) NOT NULL, reason_code VARCHAR(128) NOT NULL, triggered_at DATETIME(6) NOT NULL, summary TEXT NOT NULL, evidence_ids JSON NOT NULL, status VARCHAR(32) NOT NULL, uncertainty TEXT)""",
        )
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
        finally:
            close = getattr(cursor, "close", None)
            if close: close()
            self._close_if_owned(connection)

    @classmethod
    def _row_to_fund(cls, row: Any) -> FundShare:
        return FundShare(
            code=str(cls._row_value(row, 0, "code")),
            product_id=cls._row_value(row, 1, "product_id"),
            name=cls._row_value(row, 2, "name"),
            category=cls._row_value(row, 3, "category"),
            share_class=cls._row_value(row, 4, "share_class"),
        )

    def save_fund(self, fund: FundShare) -> FundShare:
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO funds (identity_key, code, product_id, name, category, share_class) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE product_id=VALUES(product_id), name=VALUES(name), category=VALUES(category), share_class=VALUES(share_class)",
                (fund.identity_key, fund.code, fund.product_id, fund.name, fund.category, fund.share_class),
            )
            connection.commit()
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)
        return fund

    def list_funds(self) -> list[FundShare]:
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT code, product_id, name, category, share_class FROM funds ORDER BY code, share_class")
            return [self._row_to_fund(row) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)

    def delete_fund(self, code: str) -> bool:
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            if ":" in code:
                cursor.execute("DELETE FROM funds WHERE identity_key = %s", (code,))
            else:
                cursor.execute("DELETE FROM funds WHERE code = %s", (code,))
            connection.commit()
            return bool(getattr(cursor, "rowcount", 0))
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)

    @staticmethod
    def _row_value(row: Any, index: int, key: str) -> Any:
        """Read either a default tuple cursor row or an optional dict row."""

        if isinstance(row, dict):
            return row[key]
        return row[index]

    @classmethod
    def _row_to_snapshot(cls, row: Any) -> HoldingSnapshot:
        fund = FundShare(
            code=str(cls._row_value(row, 0, "code")),
            product_id=cls._row_value(row, 1, "product_id"),
            name=cls._row_value(row, 2, "name"),
            category=cls._row_value(row, 3, "category"),
            share_class=cls._row_value(row, 4, "share_class"),
        )
        return HoldingSnapshot(
            fund=fund,
            amount=cls._row_value(row, 5, "amount"),
            units=cls._row_value(row, 6, "units"),
            invested=cls._row_value(row, 7, "invested"),
            as_of=cls._row_value(row, 8, "as_of"),
            manual_value=cls._row_value(row, 9, "manual_value"),
        )

    def save_snapshot(self, snapshot: HoldingSnapshot) -> HoldingSnapshot:
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO holding_snapshots "
                "(fund_identity_key, fund_code, amount, units, invested, as_of, manual_value) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    snapshot.fund.identity_key,
                    snapshot.fund.code,
                    snapshot.amount,
                    snapshot.units,
                    snapshot.invested,
                    snapshot.as_of,
                    snapshot.manual_value,
                ),
            )
            connection.commit()
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)
        return snapshot

    def latest_snapshots(self, subject: str | None = None) -> list[HoldingSnapshot]:
        connection = self._get_connection()
        cursor = connection.cursor()
        statement = (
            "SELECT f.code, f.product_id, f.name, f.category, f.share_class, "
            "hs.amount, hs.units, hs.invested, hs.as_of, hs.manual_value "
            "FROM holding_snapshots hs "
            "JOIN funds f ON f.identity_key = hs.fund_identity_key"
        )
        params: tuple[Any, ...] = ()
        if subject is not None:
            if ":" in subject:
                statement += " WHERE hs.fund_identity_key = %s"
                params = (subject,)
            else:
                statement += " WHERE hs.fund_code = %s"
                params = (subject,)
        statement += " ORDER BY hs.as_of DESC"
        try:
            if params:
                cursor.execute(statement, params)
            else:
                cursor.execute(statement)
            latest: dict[str, HoldingSnapshot] = {}
            for row in cursor.fetchall():
                snapshot = self._row_to_snapshot(row)
                # Keep the identity guard in Python as well as SQL so a custom
                # cursor cannot accidentally reintroduce same-code merging.
                if subject is not None and ":" in subject and snapshot.fund.identity_key != subject:
                    continue
                latest.setdefault(snapshot.fund.identity_key, snapshot)
            return list(latest.values())
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)

    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _row_to_evidence(cls, row: Any) -> Evidence:
        return Evidence(
            id=str(cls._row_value(row, 0, "id")),
            source_type=cls._row_value(row, 1, "source_type"),
            subject=str(cls._row_value(row, 2, "subject")),
            collected_at=cls._row_value(row, 3, "collected_at"),
            effective_at=cls._row_value(row, 4, "effective_at"),
            url=cls._row_value(row, 5, "url"),
            content=cls._row_value(row, 6, "content"),
            confidence=cls._row_value(row, 7, "confidence"),
            status=cls._row_value(row, 8, "status"),
            metadata=cls._json_value(cls._row_value(row, 9, "metadata"), {}),
        )

    def save_evidence(self, evidence: Evidence) -> Evidence:
        saved = evidence
        if saved.id is None:
            saved = saved.model_copy(update={"id": uuid4().hex})
        metadata = json.dumps(saved.metadata, ensure_ascii=False, separators=(",", ":"))
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO evidence "
                "(id, source_type, subject, collected_at, effective_at, url, content, confidence, status, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE source_type=VALUES(source_type), subject=VALUES(subject), "
                "collected_at=VALUES(collected_at), effective_at=VALUES(effective_at), url=VALUES(url), "
                "content=VALUES(content), confidence=VALUES(confidence), status=VALUES(status), metadata=VALUES(metadata)",
                (
                    saved.id,
                    saved.source_type.value,
                    saved.subject,
                    saved.collected_at,
                    saved.effective_at,
                    saved.url,
                    saved.content,
                    saved.confidence,
                    saved.status.value,
                    metadata,
                ),
            )
            connection.commit()
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)
        return saved

    def list_evidence(self, subject: str | None = None) -> list[Evidence]:
        connection = self._get_connection()
        cursor = connection.cursor()
        statement = (
            "SELECT id, source_type, subject, collected_at, effective_at, url, content, "
            "confidence, status, metadata FROM evidence"
        )
        params: tuple[Any, ...] = ()
        if subject is not None:
            statement += " WHERE subject = %s"
            params = (subject,)
        statement += " ORDER BY collected_at DESC"
        try:
            if params:
                cursor.execute(statement, params)
            else:
                cursor.execute(statement)
            return [self._row_to_evidence(row) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)

    @classmethod
    def _row_to_alert(cls, row: Any) -> RiskAlert:
        return RiskAlert(
            id=str(cls._row_value(row, 0, "id")),
            subject=str(cls._row_value(row, 1, "subject")),
            level=cls._row_value(row, 2, "level"),
            reason_code=str(cls._row_value(row, 3, "reason_code")),
            triggered_at=cls._row_value(row, 4, "triggered_at"),
            summary=cls._row_value(row, 5, "summary"),
            evidence_ids=cls._json_value(cls._row_value(row, 6, "evidence_ids"), []),
            status=cls._row_value(row, 7, "status"),
            uncertainty=cls._row_value(row, 8, "uncertainty"),
        )

    def save_alert(self, alert: RiskAlert) -> RiskAlert:
        saved = alert
        if saved.id is None:
            saved = saved.model_copy(update={"id": uuid4().hex})
        evidence_ids = json.dumps(saved.evidence_ids, ensure_ascii=False, separators=(",", ":"))
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO risk_alerts "
                "(id, subject, level, reason_code, triggered_at, summary, evidence_ids, status, uncertainty) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE subject=VALUES(subject), level=VALUES(level), "
                "reason_code=VALUES(reason_code), triggered_at=VALUES(triggered_at), summary=VALUES(summary), "
                "evidence_ids=VALUES(evidence_ids), status=VALUES(status), uncertainty=VALUES(uncertainty)",
                (
                    saved.id,
                    saved.subject,
                    saved.level.value,
                    saved.reason_code,
                    saved.triggered_at,
                    saved.summary,
                    evidence_ids,
                    saved.status.value,
                    saved.uncertainty,
                ),
            )
            connection.commit()
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)
        return saved

    def list_alerts(
        self,
        subject: str | None = None,
        status: AlertStatus | None = None,
    ) -> list[RiskAlert]:
        connection = self._get_connection()
        cursor = connection.cursor()
        statement = (
            "SELECT id, subject, level, reason_code, triggered_at, summary, evidence_ids, status, uncertainty "
            "FROM risk_alerts"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if subject is not None:
            conditions.append("subject = %s")
            params.append(subject)
        if status is not None:
            conditions.append("status = %s")
            params.append(status.value)
        if conditions:
            statement += " WHERE " + " AND ".join(conditions)
        statement += " ORDER BY triggered_at DESC"
        try:
            if params:
                cursor.execute(statement, tuple(params))
            else:
                cursor.execute(statement)
            return [self._row_to_alert(row) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()
            self._close_if_owned(connection)
