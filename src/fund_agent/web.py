from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from fund_agent.application import FundAgentApplication, parse_json_object
from fund_agent.domain.models import Evidence, EvidenceStatus, FundAnalysis, FundShare, HoldingSnapshot, NavMetrics, SourceType
from fund_agent.funds.identity import normalize_fund_input
from fund_agent.screening.service import PreferenceProfile


class FundInput(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    product_id: str | None = None
    name: str | None = None
    category: str | None = None
    share_class: str | None = None


class AnalysisInput(FundInput):
    metrics: NavMetrics = Field(default_factory=NavMetrics)
    quality_score: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PreferenceInput(BaseModel):
    risk: str = "balanced"
    horizon: str = "long_term"


class ScreeningInput(BaseModel):
    funds: list[AnalysisInput]
    preference: PreferenceInput | None = None


class HoldingInput(FundInput):
    amount: float | None = Field(default=None, gt=0)
    units: float | None = Field(default=None, gt=0)
    invested: float = Field(gt=0)
    as_of: datetime
    manual_value: float | None = Field(default=None, gt=0)


class TrackingInput(BaseModel):
    funds: list[AnalysisInput] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class SettingsInput(BaseModel):
    """Editable non-secret settings; omitted (null) fields keep their current value."""

    full_analysis_time: str | None = None
    analysis_timezone: str | None = None
    analysis_weekdays: list[int] | None = None
    evidence_interval_hours: float | None = Field(default=None, gt=0)
    scheduler_enabled: bool | None = None
    nav_drop_threshold: float | None = None
    drawdown_threshold: float | None = None
    volatility_threshold: float | None = None
    stale_hours: float | None = Field(default=None, gt=0)
    official_confidence: float | None = Field(default=None, ge=0, le=1)
    screening_focus_threshold: float | None = Field(default=None, ge=0, le=1)
    screening_observe_threshold: float | None = Field(default=None, ge=0, le=1)
    screening_neutral_threshold: float | None = Field(default=None, ge=0, le=1)
    screening_weights: dict[str, float] | None = None
    market_endpoint: str | None = None
    official_endpoint: str | None = None
    news_endpoint: str | None = None
    sentiment_endpoint: str | None = None


def _fund(value: FundInput) -> FundShare:
    metadata = value.model_dump(exclude={"code"})
    return normalize_fund_input(value.code, metadata)


def _analysis(value: AnalysisInput) -> FundAnalysis:
    payload = value.model_dump(exclude={"code", "product_id", "name", "category", "share_class"})
    payload["fund"] = _fund(value)
    return FundAnalysis(**payload)


def build_app(application: FundAgentApplication | None = None) -> FastAPI:
    container = application or FundAgentApplication()
    app = FastAPI(title="TouzhiAgent", version="0.1.0")
    app.state.fund_agent = container

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        path = Path(__file__).parent / "static" / "index.html"
        return path.read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/settings")
    def settings() -> dict[str, Any]:
        return container.settings.model_dump()

    @app.put("/api/settings", response_model=dict[str, Any])
    def update_settings(value: SettingsInput) -> dict[str, Any]:
        try:
            return container.update_settings(value.model_dump()).model_dump()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[{"msg": error.get("msg"), "loc": error.get("loc")} for error in exc.errors()]) from exc

    @app.post("/api/funds", response_model=FundShare, status_code=201)
    def add_fund(value: FundInput) -> FundShare:
        return container.add_fund(_fund(value))

    @app.get("/api/funds", response_model=list[FundShare])
    def list_funds() -> list[FundShare]:
        return container.list_funds()

    @app.put("/api/funds/{code}", response_model=FundShare)
    def update_fund(code: str, value: FundInput) -> FundShare:
        if code != value.code:
            raise HTTPException(status_code=422, detail="path code and body code must match")
        if not any(item.code == code for item in container.list_funds()):
            raise HTTPException(status_code=404, detail="fund not found")
        return container.add_fund(_fund(value))

    @app.delete("/api/funds/{code}", status_code=204)
    def delete_fund(code: str) -> Response:
        if not container.repository.delete_fund(code):
            raise HTTPException(status_code=404, detail="fund not found")
        return Response(status_code=204)

    @app.post("/api/screening", response_model=list[dict[str, Any]])
    def screen(value: ScreeningInput) -> list[dict[str, Any]]:
        preference = PreferenceProfile(**value.preference.model_dump()) if value.preference else None
        return [
            {"code": result.fund.code, **result.model_dump(exclude={"fund"})}
            for result in container.screen([_analysis(item) for item in value.funds], preference)
        ]

    @app.post("/api/holdings", response_model=HoldingSnapshot, status_code=201)
    def add_holding(value: HoldingInput) -> HoldingSnapshot:
        return container.add_holding(HoldingSnapshot(fund=_fund(value), **value.model_dump(exclude={"code", "product_id", "name", "category", "share_class"})))

    @app.get("/api/portfolio")
    def portfolio(latest_values: str | None = Query(default=None)) -> Any:
        try:
            values = parse_json_object(latest_values)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return container.analyze_portfolio(values).model_dump()

    @app.post("/api/tracking/run")
    def tracking(value: TrackingInput) -> dict[str, Any]:
        result = container.run_tracking([_analysis(item) for item in value.funds] or None, value.evidence)
        return result.model_dump()

    @app.get("/api/alerts", response_model=list[dict[str, Any]])
    def alerts() -> list[dict[str, Any]]:
        return [alert.model_dump() for alert in container.alerts.list_alerts()]

    return app
