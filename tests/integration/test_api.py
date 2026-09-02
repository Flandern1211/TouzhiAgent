from datetime import datetime, timezone

from fastapi.testclient import TestClient

from fund_agent.application import FundAgentApplication, create_application
from fund_agent.persistence.repository import InMemoryRepository
from fund_agent.web import build_app


def test_local_api_supports_fund_screening_portfolio_tracking_and_alerts():
    application = build_app(FundAgentApplication(repository=InMemoryRepository(), source_adapters=[]))
    client = TestClient(application)

    fund_response = client.post(
        "/api/funds",
        json={"code": "000001", "name": "示例基金 C", "category": "混合型"},
    )
    assert fund_response.status_code == 201
    assert fund_response.json()["share_class"] == "C"

    assert client.get("/api/funds").json()[0]["code"] == "000001"

    screening = client.post(
        "/api/screening",
        json={
            "funds": [
                {"code": "000001", "name": "示例基金 C", "metrics": {"total_return": 0.2, "max_drawdown": -0.1, "volatility": 0.1, "sharpe": 1.0}, "quality_score": 0.9},
            ],
            "preference": {"risk": "balanced", "horizon": "long_term"},
        },
    )
    assert screening.status_code == 200
    assert screening.json()[0]["code"] == "000001"

    holding = client.post(
        "/api/holdings",
        json={"code": "000001", "name": "示例基金 C", "amount": 1000, "invested": 900, "as_of": datetime.now(timezone.utc).isoformat()},
    )
    assert holding.status_code == 201

    portfolio = client.get("/api/portfolio", params={"latest_values": '{"000001": 1000}'})
    assert portfolio.status_code == 200
    assert portfolio.json()["total_value"] == 1000

    tracking = client.post("/api/tracking/run", json={"funds": [{"code": "000001", "metrics": {"max_drawdown": -0.3}}], "evidence": []})
    assert tracking.status_code == 200
    assert tracking.json()["subjects"] == ["000001"]
    assert tracking.json()["alerts"][0]["level"] == "observe"
    assert tracking.json()["alerts"][0]["uncertainty"]
    assert tracking.json()["reviews"]["000001"]["reason_code"] == "drawdown"

    alerts = client.get("/api/alerts")
    assert alerts.status_code == 200
    assert alerts.json()[0]["subject"] == "000001"


def test_local_api_rejects_invalid_fund_and_has_health_route():
    client = TestClient(create_application())

    response = client.post("/api/funds", json={"code": "bad"})
    assert response.status_code == 422
    assert client.get("/api/health").json() == {"status": "ok"}


def test_local_api_updates_and_deletes_manual_fund_candidates():
    client = TestClient(create_application())
    client.post("/api/funds", json={"code": "000001", "name": "旧名称"})

    updated = client.put("/api/funds/000001", json={"code": "000001", "name": "新名称 E"})
    deleted = client.delete("/api/funds/000001")

    assert updated.status_code == 200
    assert updated.json()["name"] == "新名称 E"
    assert updated.json()["share_class"] == "E"
    assert deleted.status_code == 204
    assert client.get("/api/funds").json() == []


def test_local_api_serves_browser_view():
    response = TestClient(create_application()).get("/")

    assert response.status_code == 200
    assert "TouzhiAgent" in response.text
    assert "持仓快照" in response.text
    assert "基金筛选" in response.text
    assert "运行跟踪" in response.text
    assert "innerHTML" not in response.text
    assert "/api/settings" in response.text


def test_local_api_tracking_can_use_repository_funds_and_reports_source_status():
    application = create_application()
    client = TestClient(application)
    client.post("/api/funds", json={"code": "000001", "name": "示例基金 C"})

    response = client.post("/api/tracking/run", json={})

    assert response.status_code == 200
    assert response.json()["subjects"] == ["000001"]
    assert "market" in response.json()["source_statuses"]
    assert response.json()["source_statuses"]["market"]["status"] in {"available", "estimated", "failed"}


def test_local_api_exposes_redacted_runtime_settings():
    response = TestClient(create_application()).get("/api/settings")

    assert response.status_code == 200
    assert "database_url" not in response.json()
    assert "crawler_api_key" not in response.json()
    assert "evidence_interval_hours" in response.json()
    assert response.json()["crawler_timeout_seconds"] == 10.0
    assert response.json()["crawler_max_retries"] == 2
    assert response.json()["crawler_max_response_bytes"] == 2_000_000
    assert response.json()["crawler_min_interval_seconds"] == 0.25
