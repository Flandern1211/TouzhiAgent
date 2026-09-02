from datetime import datetime, timedelta, timezone
import json

from fund_agent.domain.models import (
    AlertStatus,
    Evidence,
    EvidenceStatus,
    FundShare,
    HoldingSnapshot,
    RiskAlert,
    RiskLevel,
    SourceType,
)
from fund_agent.persistence.mysql import MySqlRepository


class Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.closed = False
        self.rowcount = 1

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, rows=None):
        self.cursor_instance = Cursor(rows)
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class ClosableConnection(Connection):
    def __init__(self, rows=None):
        super().__init__(rows)
        self.closed = False

    def close(self):
        self.closed = True


NOW = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)


def test_mysql_repository_round_trips_fund_with_parameterized_sql():
    connection = Connection([("000001", "product-1", "示例基金 C", "混合型", "C")])
    repository = MySqlRepository(connection=connection)
    fund = FundShare(code="000001", product_id="product-1", name="示例基金 C", category="混合型", share_class="C")

    assert repository.save_fund(fund) == fund
    assert repository.list_funds() == [fund]
    assert connection.commits == 1
    parameterized = [
        (statement, params)
        for statement, params in connection.cursor_instance.statements
        if statement.startswith("INSERT")
    ]
    assert parameterized and "%s" in parameterized[0][0]
    assert parameterized[0][1] == ("000001:C", "000001", "product-1", "示例基金 C", "混合型", "C")
    assert connection.cursor_instance.closed is True


def test_mysql_repository_keeps_same_code_share_classes_distinct_in_fund_listing():
    connection = Connection([
        ("000001", "product-1", "示例基金 A", "混合型", "A"),
        ("000001", "product-1", "示例基金 C", "混合型", "C"),
    ])
    repository = MySqlRepository(connection=connection)

    funds = repository.list_funds()

    assert [fund.identity_key for fund in funds] == ["000001:A", "000001:C"]


def test_mysql_repository_filters_latest_snapshots_by_identity_key_without_merging_share_classes():
    rows = [
        ("000001", "product-1", "示例基金 A", "混合型", "A", 100.0, None, 90.0, NOW, None),
        ("000001", "product-1", "示例基金 C", "混合型", "C", 200.0, None, 180.0, NOW, None),
    ]
    repository = MySqlRepository(connection=Connection(rows))

    all_latest = repository.latest_snapshots()
    class_a = repository.latest_snapshots("000001:A")

    assert {snapshot.fund.identity_key for snapshot in all_latest} == {"000001:A", "000001:C"}
    assert [snapshot.fund.identity_key for snapshot in class_a] == ["000001:A"]
    statement, params = repository._connection.cursor_instance.statements[-1]
    assert "WHERE hs.fund_identity_key = %s" in statement
    assert params == ("000001:A",)


def test_mysql_repository_closes_connections_created_by_connect_after_each_operation():
    created: list[ClosableConnection] = []

    def connect():
        connection = ClosableConnection()
        created.append(connection)
        return connection

    repository = MySqlRepository(connect=connect)
    repository.initialize_schema()
    repository.save_fund(FundShare(code="000001"))
    repository.list_funds()
    repository.delete_fund("000001")
    repository.save_snapshot(HoldingSnapshot(
        fund=FundShare(code="000001"), amount=100, invested=90, as_of=NOW,
    ))
    repository.latest_snapshots()
    repository.save_evidence(Evidence(
        source_type=SourceType.MARKET, subject="000001", collected_at=NOW, confidence=1,
    ))
    repository.list_evidence()
    repository.save_alert(RiskAlert(
        subject="000001", level=RiskLevel.OBSERVE, reason_code="x", triggered_at=NOW, summary="x",
    ))
    repository.list_alerts()

    assert len(created) == 10
    assert all(connection.closed for connection in created)
    assert all(connection.cursor_instance.closed for connection in created)


def test_mysql_repository_does_not_close_injected_connection():
    connection = ClosableConnection()
    repository = MySqlRepository(connection=connection)

    repository.save_fund(FundShare(code="000001"))
    repository.list_funds()

    assert connection.closed is False


def test_mysql_repository_reads_funds_from_dict_cursor_rows():
    connection = Connection([
        {"code": "000001", "product_id": "product-1", "name": "示例基金 C", "category": "混合型", "share_class": "C"}
    ])
    repository = MySqlRepository(connection=connection)

    assert repository.list_funds() == [
        FundShare(code="000001", product_id="product-1", name="示例基金 C", category="混合型", share_class="C")
    ]


def test_mysql_repository_deletes_fund_with_parameterized_sql_and_returns_rowcount():
    connection = Connection()
    connection.cursor_instance.rowcount = 1
    repository = MySqlRepository(connection=connection)

    assert repository.delete_fund("000001") is True

    statement, params = connection.cursor_instance.statements[-1]
    assert statement == "DELETE FROM funds WHERE code = %s"
    assert params == ("000001",)
    assert connection.commits == 1
    assert connection.cursor_instance.closed is True


def test_mysql_repository_saves_snapshot_and_reads_latest_per_fund():
    fund = FundShare(code="000001", name="示例基金")
    snapshot = HoldingSnapshot(fund=fund, units=10, invested=900, as_of=NOW, manual_value=1000)
    connection = Connection()
    repository = MySqlRepository(connection=connection)

    assert repository.save_snapshot(snapshot) == snapshot

    save_statement, save_params = connection.cursor_instance.statements[-1]
    assert save_statement.startswith("INSERT INTO holding_snapshots")
    assert "%s" in save_statement
    assert save_params == ("000001:-", "000001", None, 10.0, 900.0, NOW, 1000.0)
    assert connection.commits == 1
    assert connection.cursor_instance.closed is True

    latest = HoldingSnapshot(
        fund=FundShare(code="000001", name="示例基金", category="混合型", share_class="C"),
        amount=1200,
        invested=900,
        as_of=NOW,
    )
    connection.cursor_instance.rows = [
        ("000001", None, "示例基金", "混合型", "C", 1200.0, None, 900.0, NOW, None),
    ]
    assert repository.latest_snapshots("000001") == [latest]
    statement, params = connection.cursor_instance.statements[-1]
    assert "ORDER BY hs.as_of DESC" in statement
    assert "WHERE hs.fund_code = %s" in statement
    assert params == ("000001",)
    assert connection.cursor_instance.closed is True


def test_mysql_repository_saves_and_lists_evidence_with_json_metadata():
    evidence = Evidence(
        id="e-1",
        source_type=SourceType.OFFICIAL,
        subject="000001",
        collected_at=NOW,
        effective_at=NOW - timedelta(days=1),
        url="https://example.test/notice",
        content="公告内容",
        confidence=0.95,
        status=EvidenceStatus.AVAILABLE,
        metadata={"nav": [1.0, 1.1], "verified": True},
    )
    connection = Connection()
    repository = MySqlRepository(connection=connection)

    assert repository.save_evidence(evidence) == evidence

    save_statement, save_params = connection.cursor_instance.statements[-1]
    assert save_statement.startswith("INSERT INTO evidence")
    assert "%s" in save_statement
    assert save_params[-1] == json.dumps(evidence.metadata, ensure_ascii=False, separators=(",", ":"))
    assert connection.commits == 1
    assert connection.cursor_instance.closed is True

    connection.cursor_instance.rows = [
        (
            "e-1", "official", "000001", NOW, NOW - timedelta(days=1),
            "https://example.test/notice", "公告内容", 0.95, "available",
            json.dumps(evidence.metadata, ensure_ascii=False),
        )
    ]
    assert repository.list_evidence("000001") == [evidence]
    statement, params = connection.cursor_instance.statements[-1]
    assert "WHERE subject = %s" in statement
    assert params == ("000001",)
    assert connection.cursor_instance.closed is True


def test_mysql_repository_saves_and_filters_alerts_with_json_evidence_ids():
    alert = RiskAlert(
        id="a-1",
        subject="000001",
        level=RiskLevel.HIGH_RISK,
        reason_code="drawdown",
        triggered_at=NOW,
        summary="回撤超过阈值",
        evidence_ids=["e-1", "e-2"],
        status=AlertStatus.ACTIVE,
        uncertainty="待进一步核查",
    )
    connection = Connection()
    repository = MySqlRepository(connection=connection)

    assert repository.save_alert(alert) == alert

    save_statement, save_params = connection.cursor_instance.statements[-1]
    assert save_statement.startswith("INSERT INTO risk_alerts")
    assert "%s" in save_statement
    assert save_params[6] == json.dumps(alert.evidence_ids, ensure_ascii=False, separators=(",", ":"))
    assert connection.commits == 1
    assert connection.cursor_instance.closed is True

    connection.cursor_instance.rows = [
        (
            "a-1", "000001", "high_risk", "drawdown", NOW, "回撤超过阈值",
            json.dumps(["e-1", "e-2"], ensure_ascii=False), "active", "待进一步核查",
        )
    ]
    assert repository.list_alerts("000001", status=AlertStatus.ACTIVE) == [alert]
    statement, params = connection.cursor_instance.statements[-1]
    assert "WHERE subject = %s AND status = %s" in statement
    assert params == ("000001", "active")
    assert connection.cursor_instance.closed is True


def test_mysql_repository_schema_is_v1_scoped():
    connection = Connection()
    repository = MySqlRepository(connection=connection)

    repository.initialize_schema()

    statements = [statement for statement, _ in connection.cursor_instance.statements]
    assert len(statements) == 4
    assert all(statement.startswith("CREATE TABLE IF NOT EXISTS") for statement in statements)
    assert "FOREIGN KEY (fund_code)" not in statements[1]


def test_repository_does_not_close_injected_connection():
    connection = Connection()
    repository = MySqlRepository(connection=connection)

    repository.initialize_schema()

    assert connection.closed is False


def test_repository_closes_runtime_factory_connections_after_operation():
    connection = Connection()
    repository = MySqlRepository(connect=lambda: connection)

    repository.list_funds()

    assert connection.closed is True
